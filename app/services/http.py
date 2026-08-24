from __future__ import annotations

import atexit
import ipaddress
import logging
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from typing import Protocol, TypeVar
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)
settings = get_settings()

# Domains with known SSL issues that should use relaxed verification
SSL_BYPASS_DOMAINS: set[str] = {
    "0x80.pl",
    # Add other problematic domains here
}

# HTTP status codes that should never be retried
NON_RETRYABLE_STATUS_CODES: set[int] = {
    400,
    401,
    403,
    404,
    405,
    406,
    407,
    408,
    409,
    410,
    411,
    412,
    413,
    414,
    415,
    416,
    417,
    418,
    421,
    422,
    423,
    424,
    425,
    426,
    428,
    429,
    431,
    451,  # Client errors
}

DEFAULT_BOUNDED_RESPONSE_BYTES = 2_000_000
DEFAULT_MAX_REDIRECTS = 5
_BoundedResult = TypeVar("_BoundedResult")


class NonRetryableError(Exception):
    """Exception for errors that should not be retried."""

    pass


class UnsafeHttpUrlError(NonRetryableError):
    """Raised when a host download could reach a non-public network target."""


class ResponseTooLargeError(NonRetryableError):
    """Raised when a streamed response exceeds its configured byte budget."""


def _bounded_response_chunks(
    response: httpx.Response,
    *,
    max_response_bytes: int | None,
) -> Iterator[bytes]:
    byte_count = 0
    for chunk in response.iter_bytes():
        byte_count += len(chunk)
        if max_response_bytes is not None and byte_count > max_response_bytes:
            raise ResponseTooLargeError(f"Response exceeds {max_response_bytes} byte limit")
        yield chunk


def _resolve_host_addresses(host: str, port: int) -> set[str]:
    """Resolve a host for public-network validation before dispatch."""
    return {str(result[4][0]) for result in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}


def _resolve_public_http_addresses(url: str) -> tuple[str, ...]:
    """Resolve and return only public unicast addresses for an HTTP target."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeHttpUrlError("Only HTTP and HTTPS URLs may be downloaded")
    if not parsed.hostname:
        raise UnsafeHttpUrlError("Download URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeHttpUrlError("Credentialed download URLs are not allowed")

    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise UnsafeHttpUrlError("Download URL has an invalid port") from exc

    try:
        addresses = _resolve_host_addresses(parsed.hostname, port)
    except OSError as exc:
        raise NonRetryableError(f"DNS resolution error: {exc}") from exc
    if not addresses:
        raise NonRetryableError(f"DNS resolution returned no addresses for {parsed.hostname}")

    public_addresses: list[str] = []
    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UnsafeHttpUrlError(
                f"DNS returned an invalid address for {parsed.hostname}"
            ) from exc
        if not parsed_address.is_global or parsed_address.is_multicast:
            raise UnsafeHttpUrlError(
                f"Download target {parsed.hostname} resolves to a non-public address"
            )
        public_addresses.append(str(parsed_address))
    return tuple(sorted(public_addresses))


def should_bypass_ssl(url: str) -> bool:
    """Check if URL domain should bypass SSL verification."""
    try:
        domain = (urlparse(url).hostname or "").lower()
        return any(
            domain == bypass_domain or domain.endswith(f".{bypass_domain}")
            for bypass_domain in SSL_BYPASS_DOMAINS
        )
    except Exception:
        return False


def is_ssl_error(error: Exception) -> bool:
    """Check if error is SSL-related."""
    error_str = str(error).lower()
    return any(
        ssl_term in error_str
        for ssl_term in ["ssl", "certificate", "hostname mismatch", "cert", "tls"]
    )


def is_dns_resolution_error(error: Exception) -> bool:
    """Check if a connection error came from hostname resolution."""
    error_str = str(error).lower()
    return any(
        dns_term in error_str
        for dns_term in [
            "nodename nor servname provided",
            "name or service not known",
            "temporary failure in name resolution",
            "no address associated with hostname",
            "getaddrinfo failed",
            "failed to resolve",
        ]
    )


def categorize_http_error(error: httpx.HTTPStatusError) -> Exception:
    """Categorize HTTP errors into retryable vs non-retryable."""
    status_code = error.response.status_code

    if status_code in NON_RETRYABLE_STATUS_CODES:
        return NonRetryableError(f"Non-retryable HTTP {status_code}: {error}")

    # 5xx errors are generally retryable
    if 500 <= status_code < 600:
        return error

    # Default to non-retryable for unknown status codes
    return NonRetryableError(f"Unknown status code {status_code}: {error}")


def _is_retryable_http_error(error: BaseException) -> bool:
    """Return whether a failed request is safe to retry."""
    if isinstance(error, NonRetryableError):
        return False
    if isinstance(error, httpx.HTTPStatusError):
        return 500 <= error.response.status_code < 600
    return isinstance(error, httpx.TransportError)


class HttpFetcher(Protocol):
    """Minimal transport contract used by feed and discussion probes."""

    def fetch(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        log_client_errors: bool = True,
        log_exceptions: bool = True,
    ) -> httpx.Response: ...


class BoundedPublicHttpService:
    """HttpFetcher adapter that always applies SSRF, redirect, and byte bounds."""

    def __init__(self, service: HttpService | None = None) -> None:
        self._service = service or get_http_service()

    def fetch(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        log_client_errors: bool = True,
        log_exceptions: bool = True,
    ) -> httpx.Response:
        return self._service.fetch_bounded_public(
            url,
            headers=headers,
            log_client_errors=log_client_errors,
            log_exceptions=log_exceptions,
        )


def fetch_quiet(
    http_service: HttpFetcher,
    url: str,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Fetch once while suppressing expected probe failures from error logs."""
    return http_service.fetch(
        url,
        headers=headers,
        log_client_errors=False,
        log_exceptions=False,
    )


class HttpService:
    """HTTP client with intelligent retry logic and SSL handling."""

    def __init__(self):
        self.timeout = httpx.Timeout(timeout=settings.http_timeout_seconds, connect=10.0)
        # Enhanced user agent to avoid bot detection
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
        }
        self._clients: dict[tuple[bool, bool], httpx.Client] = {}
        self._clients_lock = threading.Lock()

    def get_client(
        self,
        url: str | None = None,
        *,
        trust_env: bool = True,
    ) -> httpx.Client:
        """Return a reusable client with the URL's required SSL policy."""
        verify_ssl = not (url and should_bypass_ssl(url))
        if not verify_ssl:
            logger.warning("Bypassing SSL verification for %s", urlparse(url).netloc)

        client_key = (verify_ssl, trust_env)
        with self._clients_lock:
            client = self._clients.get(client_key)
            if client is None or client.is_closed:
                client = httpx.Client(
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers=self.headers,
                    verify=verify_ssl,
                    trust_env=trust_env,
                )
                self._clients[client_key] = client
            return client

    def close(self) -> None:
        """Close every pooled client owned by this service."""
        with self._clients_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            if not client.is_closed:
                client.close()

    def _consume_bounded_public(
        self,
        url: str,
        *,
        headers: dict[str, str] | None,
        max_response_bytes: int | None,
        max_redirects: int,
        consumer: Callable[
            [httpx.Response, httpx.URL, dict[str, str], Iterator[bytes]],
            _BoundedResult,
        ],
        log_client_errors: bool,
        log_exceptions: bool,
    ) -> _BoundedResult:
        """Validate, pin, redirect, and bound one public streaming request."""
        if max_response_bytes is not None and max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if max_redirects < 0:
            raise ValueError("max_redirects must be non-negative")

        request_headers = self.headers.copy()
        if headers:
            request_headers.update(headers)
        current_url = url
        try:
            for redirect_count in range(max_redirects + 1):
                addresses = _resolve_public_http_addresses(current_url)
                original_url = httpx.URL(current_url)
                client = self.get_client(current_url, trust_env=False)
                hop_headers = {
                    key: value for key, value in request_headers.items() if key.lower() != "host"
                }
                hop_headers["Host"] = original_url.netloc.decode("ascii")
                hop_headers["Connection"] = "close"
                redirect_location: str | None = None
                last_transport_error: httpx.TransportError | None = None

                for address in addresses:
                    pinned_url = original_url.copy_with(host=address)
                    logger.debug("Fetching bounded public URL %s via %s", current_url, address)
                    try:
                        with client.stream(
                            "GET",
                            pinned_url,
                            headers=hop_headers,
                            follow_redirects=False,
                            extensions={"sni_hostname": original_url.host},
                        ) as response:
                            if response.has_redirect_location:
                                if redirect_count >= max_redirects:
                                    raise NonRetryableError(
                                        f"HTTP redirect limit exceeded for {url}"
                                    )
                                redirect_location = response.headers["location"]
                                break
                            response.raise_for_status()
                            content_length = response.headers.get("content-length")
                            if content_length is not None:
                                try:
                                    declared_bytes = int(content_length)
                                except ValueError:
                                    declared_bytes = 0
                                if (
                                    max_response_bytes is not None
                                    and declared_bytes > max_response_bytes
                                ):
                                    raise ResponseTooLargeError(
                                        f"Response exceeds {max_response_bytes} byte limit"
                                    )
                            return consumer(
                                response,
                                original_url,
                                hop_headers,
                                _bounded_response_chunks(
                                    response,
                                    max_response_bytes=max_response_bytes,
                                ),
                            )
                    except httpx.TransportError as exc:
                        last_transport_error = exc

                if redirect_location is not None:
                    current_url = urljoin(current_url, redirect_location)
                    continue
                if last_transport_error is not None:
                    raise last_transport_error
                raise RuntimeError("Bounded HTTP request exhausted resolved addresses")
        except httpx.HTTPStatusError as exc:
            categorized_error = categorize_http_error(exc)
            status_code = exc.response.status_code
            if status_code >= 500 or log_client_errors:
                level = logging.ERROR if status_code >= 500 else logging.DEBUG
                logger.log(level, "HTTP error %s for %s", status_code, current_url)
            raise categorized_error from exc
        except httpx.ConnectError as exc:
            if is_ssl_error(exc):
                raise NonRetryableError(f"SSL error: {exc}") from exc
            if is_dns_resolution_error(exc):
                raise NonRetryableError(f"DNS resolution error: {exc}") from exc
            if log_exceptions:
                logger.exception("Connection error for bounded URL %s", current_url)
            raise
        except Exception:
            if log_exceptions:
                logger.exception("Bounded HTTP request error for %s", current_url)
            raise
        raise RuntimeError("Bounded HTTP request ended without a response")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception(_is_retryable_http_error),
        reraise=True,
    )
    def fetch(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        log_client_errors: bool = True,
        log_exceptions: bool = True,
    ) -> httpx.Response:
        """
        Fetch a URL with intelligent retry logic.

        Args:
            url: URL to fetch
            headers: Additional headers
            log_client_errors: Whether to log 4xx responses as errors
            log_exceptions: Whether to log exception stack traces

        Returns:
            httpx.Response object
        """
        with nullcontext(self.get_client(url)) as client:
            logger.debug(f"Fetching URL: {url}")

            request_headers = self.headers.copy()
            if headers:
                request_headers.update(headers)

            try:
                response = client.get(url, headers=request_headers)
                response.raise_for_status()

                logger.debug(f"Successfully fetched {url}: {response.status_code}")
                return response

            except httpx.HTTPStatusError as e:
                # Categorize HTTP errors
                categorized_error = categorize_http_error(e)
                status_code = e.response.status_code

                if status_code >= 500 or log_client_errors:
                    level = logging.ERROR if status_code >= 500 else logging.DEBUG
                    logger.log(
                        level,
                        "HTTP error %s for %s",
                        status_code,
                        url,
                        extra={
                            "component": "http_service",
                            "operation": "http_fetch",
                            "context_data": {"url": url, "status_code": status_code},
                        },
                    )

                # Raise categorized error (may be NonRetryableError)
                raise categorized_error from e

            except httpx.ConnectError as e:
                # Check if this is an SSL error that shouldn't be retried
                if is_ssl_error(e):
                    logger.warning(
                        "SSL error for %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_fetch",
                            "context_data": {"url": url, "error_type": "ssl_error"},
                        },
                    )
                    raise NonRetryableError(f"SSL error: {e}") from e

                if is_dns_resolution_error(e):
                    log_method = logger.warning if log_exceptions else logger.debug
                    log_method(
                        "DNS resolution error for %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_fetch",
                            "context_data": {"url": url, "error_type": "dns_resolution_error"},
                        },
                    )
                    raise NonRetryableError(f"DNS resolution error: {e}") from e

                if log_exceptions:
                    logger.exception(
                        "Connection error for %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_fetch",
                            "context_data": {"url": url, "error_type": "connection_error"},
                        },
                    )
                else:
                    logger.debug(
                        "Connection error for %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_fetch",
                            "context_data": {"url": url, "error_type": "connection_error"},
                        },
                    )
                raise

            except Exception as e:
                if log_exceptions:
                    logger.exception(
                        "HTTP fetch error for %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_fetch",
                            "context_data": {"url": url},
                        },
                    )
                else:
                    logger.debug(
                        "HTTP fetch error for %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_fetch",
                            "context_data": {"url": url},
                        },
                    )
                raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception(_is_retryable_http_error),
        reraise=True,
    )
    def fetch_bounded_public(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        max_response_bytes: int | None = DEFAULT_BOUNDED_RESPONSE_BYTES,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        log_client_errors: bool = True,
        log_exceptions: bool = True,
    ) -> httpx.Response:
        """Stream a public URL with strict redirect and optional response bounds."""

        def materialize(
            response: httpx.Response,
            original_url: httpx.URL,
            hop_headers: dict[str, str],
            chunks: Iterator[bytes],
        ) -> httpx.Response:
            body = b"".join(chunks)
            materialized_headers = [
                (key, value)
                for key, value in response.headers.multi_items()
                if key.lower() not in {"content-encoding", "content-length"}
            ]
            return httpx.Response(
                response.status_code,
                headers=materialized_headers,
                content=body,
                request=httpx.Request("GET", original_url, headers=hop_headers),
            )

        return self._consume_bounded_public(
            url,
            headers=headers,
            max_response_bytes=max_response_bytes,
            max_redirects=max_redirects,
            consumer=materialize,
            log_client_errors=log_client_errors,
            log_exceptions=log_exceptions,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception(_is_retryable_http_error),
        reraise=True,
    )
    def download_bounded_public(
        self,
        url: str,
        destination: str,
        *,
        max_response_bytes: int,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        headers: dict[str, str] | None = None,
    ) -> int:
        """Stream a public URL to one host file with the same SSRF boundaries."""

        def write_file(
            _response: httpx.Response,
            _original_url: httpx.URL,
            _hop_headers: dict[str, str],
            chunks: Iterator[bytes],
        ) -> int:
            byte_count = 0
            with open(destination, "wb") as output:  # noqa: PTH123
                for chunk in chunks:
                    byte_count += len(chunk)
                    output.write(chunk)
            if byte_count <= 0:
                raise NonRetryableError("Remote media response was empty")
            return byte_count

        return self._consume_bounded_public(
            url,
            headers=headers,
            max_response_bytes=max_response_bytes,
            max_redirects=max_redirects,
            consumer=write_file,
            log_client_errors=True,
            log_exceptions=True,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception(_is_retryable_http_error),
        reraise=True,
    )
    def head(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        allow_statuses: set[int] | None = None,
        *,
        log_client_errors: bool = True,
        log_exceptions: bool = True,
    ) -> httpx.Response:
        """
        Perform an HTTP HEAD request with intelligent retry logic.

        Args:
            url: URL to fetch
            headers: Additional headers
            allow_statuses: HTTP statuses that should not raise
            log_client_errors: Whether to log 4xx responses as errors
            log_exceptions: Whether to log exception stack traces

        Returns:
            httpx.Response object
        """
        with nullcontext(self.get_client(url)) as client:
            logger.debug(f"Fetching HEAD: {url}")

            request_headers = self.headers.copy()
            if headers:
                request_headers.update(headers)

            try:
                response = client.head(url, headers=request_headers)
                if allow_statuses and response.status_code in allow_statuses:
                    return response

                response.raise_for_status()

                logger.debug(f"Successfully fetched HEAD {url}: {response.status_code}")
                return response

            except httpx.HTTPStatusError as e:
                categorized_error = categorize_http_error(e)
                status_code = e.response.status_code
                if status_code >= 500 or log_client_errors:
                    level = logging.ERROR if status_code >= 500 else logging.DEBUG

                    logger.log(
                        level,
                        "HTTP error %s for HEAD %s",
                        status_code,
                        url,
                        extra={
                            "component": "http_service",
                            "operation": "http_head",
                            "context_data": {"url": url, "status_code": status_code},
                        },
                    )

                raise categorized_error from e

            except httpx.ConnectError as e:
                if is_ssl_error(e):
                    logger.warning(
                        "SSL error for HEAD %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_head",
                            "context_data": {"url": url, "error_type": "ssl_error"},
                        },
                    )
                    raise NonRetryableError(f"SSL error: {e}") from e

                if is_dns_resolution_error(e):
                    log_method = logger.warning if log_exceptions else logger.debug
                    log_method(
                        "DNS resolution error for HEAD %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_head",
                            "context_data": {"url": url, "error_type": "dns_resolution_error"},
                        },
                    )
                    raise NonRetryableError(f"DNS resolution error: {e}") from e

                if log_exceptions:
                    logger.exception(
                        "Connection error for HEAD %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_head",
                            "context_data": {"url": url, "error_type": "connection_error"},
                        },
                    )
                else:
                    logger.debug(
                        "Connection error for HEAD %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_head",
                            "context_data": {"url": url, "error_type": "connection_error"},
                        },
                    )
                raise

            except Exception as e:
                if log_exceptions:
                    logger.exception(
                        "HTTP HEAD error for %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_head",
                            "context_data": {"url": url},
                        },
                    )
                else:
                    logger.debug(
                        "HTTP HEAD error for %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_head",
                            "context_data": {"url": url},
                        },
                    )
                raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception(_is_retryable_http_error),
        reraise=True,
    )
    def fetch_content(
        self, url: str, headers: dict[str, str] | None = None
    ) -> tuple[str | bytes, dict[str, str]]:
        """
        Fetch content synchronously and return both content and headers.

        Returns:
            Tuple of (content, response_headers)
        """
        with nullcontext(self.get_client(url)) as client:
            logger.debug(f"Fetching URL (sync): {url}")

            request_headers = self.headers.copy()
            if headers:
                request_headers.update(headers)

            try:
                response = client.get(url, headers=request_headers)
                response.raise_for_status()

                logger.debug(f"Successfully fetched {url}: {response.status_code}")

                # Try to decode as text
                content_type = response.headers.get("Content-Type", "")
                content: str | bytes
                if "text" in content_type or "html" in content_type or "xml" in content_type:
                    content = response.text
                else:
                    content = response.content

                return content, dict(response.headers)

            except httpx.HTTPStatusError as e:
                # Categorize HTTP errors
                categorized_error = categorize_http_error(e)

                # Log the error
                logger.error(
                    "HTTP error %s for %s",
                    e.response.status_code,
                    url,
                    extra={
                        "component": "http_service",
                        "operation": "http_fetch",
                        "context_data": {"url": url, "status_code": e.response.status_code},
                    },
                )

                # Raise categorized error (may be NonRetryableError)
                raise categorized_error from e

            except httpx.ConnectError as e:
                # Check if this is an SSL error that shouldn't be retried
                if is_ssl_error(e):
                    logger.warning(
                        "SSL error for %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_fetch",
                            "context_data": {"url": url, "error_type": "ssl_error"},
                        },
                    )
                    raise NonRetryableError(f"SSL error: {e}") from e

                if is_dns_resolution_error(e):
                    logger.warning(
                        "DNS resolution error for %s: %s",
                        url,
                        e,
                        extra={
                            "component": "http_service",
                            "operation": "http_fetch",
                            "context_data": {"url": url, "error_type": "dns_resolution_error"},
                        },
                    )
                    raise NonRetryableError(f"DNS resolution error: {e}") from e

                logger.exception(
                    "Connection error for %s: %s",
                    url,
                    e,
                    extra={
                        "component": "http_service",
                        "operation": "http_fetch",
                        "context_data": {"url": url, "error_type": "connection_error"},
                    },
                )
                raise

            except Exception as e:
                logger.exception(
                    "HTTP fetch error for %s: %s",
                    url,
                    e,
                    extra={
                        "component": "http_service",
                        "operation": "http_fetch",
                        "context_data": {"url": url},
                    },
                )
                raise


# Global instance
_http_service: HttpService | None = None
_http_service_lock = threading.Lock()


def get_http_service() -> HttpService:
    """Get the global HTTP service instance."""
    global _http_service
    if _http_service is None:
        with _http_service_lock:
            if _http_service is None:
                _http_service = HttpService()
    return _http_service


def close_http_service() -> None:
    """Close and reset the process-wide HTTP service."""
    global _http_service
    with _http_service_lock:
        service = _http_service
        _http_service = None
    if service is not None:
        service.close()


def reset_http_service_for_testing() -> None:
    """Reset process-wide HTTP state between tests."""
    close_http_service()


atexit.register(close_http_service)
