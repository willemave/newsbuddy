import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

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


class NonRetryableError(Exception):
    """Exception for errors that should not be retried."""

    pass


def should_bypass_ssl(url: str) -> bool:
    """Check if URL domain should bypass SSL verification."""
    try:
        domain = urlparse(url).netloc.lower()
        return any(domain.endswith(bypass_domain) for bypass_domain in SSL_BYPASS_DOMAINS)
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


def fetch_quiet_compat(
    http_service: Any,
    url: str,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Fetch with quiet probe flags when the injected service supports them."""
    kwargs: dict[str, Any] = {
        "log_client_errors": False,
        "log_exceptions": False,
    }
    if headers is not None:
        kwargs["headers"] = headers
    try:
        return http_service.fetch(url, **kwargs)
    except TypeError:
        if headers is None:
            return http_service.fetch(url)
        return http_service.fetch(url, headers=headers)


def head_quiet_compat(
    http_service: Any,
    url: str,
    headers: dict[str, str] | None = None,
    allow_statuses: set[int] | None = None,
) -> httpx.Response:
    """HEAD with quiet probe flags when the injected service supports them."""
    kwargs: dict[str, Any] = {
        "log_client_errors": False,
        "log_exceptions": False,
    }
    if headers is not None:
        kwargs["headers"] = headers
    if allow_statuses is not None:
        kwargs["allow_statuses"] = allow_statuses
    try:
        return http_service.head(url, **kwargs)
    except TypeError:
        if headers is not None:
            return http_service.head(url, headers=headers, allow_statuses=allow_statuses)
        if allow_statuses is not None:
            return http_service.head(url, allow_statuses=allow_statuses)
        return http_service.head(url)


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

    def get_client(self, url: str | None = None) -> httpx.Client:
        """Get an HTTP client with appropriate SSL settings."""
        # Determine SSL verification settings
        verify_ssl = True
        if url and should_bypass_ssl(url):
            verify_ssl = False
            logger.warning(f"Bypassing SSL verification for {urlparse(url).netloc}")

        return httpx.Client(
            timeout=self.timeout, follow_redirects=True, headers=self.headers, verify=verify_ssl
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_not_exception_type((NonRetryableError, httpx.HTTPStatusError)),
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
        with self.get_client(url) as client:
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
        retry=retry_if_not_exception_type((NonRetryableError, httpx.HTTPStatusError)),
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
        with self.get_client(url) as client:
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

    def fetch_content(
        self, url: str, headers: dict[str, str] | None = None
    ) -> tuple[str | bytes, dict[str, str]]:
        """
        Fetch content synchronously and return both content and headers.

        Returns:
            Tuple of (content, response_headers)
        """
        # Determine SSL verification settings
        verify_ssl = True
        if url and should_bypass_ssl(url):
            verify_ssl = False
            logger.warning(f"Bypassing SSL verification for {urlparse(url).netloc}")

        with httpx.Client(
            timeout=self.timeout, follow_redirects=True, headers=self.headers, verify=verify_ssl
        ) as client:
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
_http_service = None


def get_http_service() -> HttpService:
    """Get the global HTTP service instance."""
    global _http_service
    if _http_service is None:
        _http_service = HttpService()
    return _http_service
