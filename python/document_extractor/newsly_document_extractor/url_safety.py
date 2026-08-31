"""Public-network-only URL validation and bounded HTTP fetching."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx


@dataclass(frozen=True, slots=True)
class PublicFetch:
    final_url: str
    body: bytes
    content_type: str | None


class UrlSafetyError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_url",
        retryable: bool = False,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status


def _is_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_global and not address.is_multicast


async def require_public_url(url: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    """Resolve a URL and reject credentials, private literals, and non-public DNS answers."""

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise UrlSafetyError("Only http and https URLs are accepted")
    if parsed.username is not None or parsed.password is not None:
        raise UrlSafetyError("URLs containing credentials are not accepted")
    if not parsed.hostname:
        raise UrlSafetyError("URL hostname is required")

    host = parsed.hostname.rstrip(".")
    try:
        literal_address = ipaddress.ip_address(host)
    except ValueError:
        literal_address = None
    if literal_address is not None:
        if not _is_public_ip(literal_address):
            raise UrlSafetyError("URL resolves to a non-public address")
        return (literal_address,)

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UrlSafetyError("URL port is invalid") from exc
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            port,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UrlSafetyError(
            "URL hostname could not be resolved",
            code="fetch_failed",
            retryable=True,
        ) from exc

    addresses = tuple(
        dict.fromkeys(ipaddress.ip_address(record[4][0]) for record in records if record[4])
    )
    if not addresses:
        raise UrlSafetyError(
            "URL hostname returned no addresses",
            code="fetch_failed",
            retryable=True,
        )
    if any(not _is_public_ip(address) for address in addresses):
        raise UrlSafetyError("URL hostname resolves to a non-public address")
    return addresses


async def fetch_public_document(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    max_redirects: int,
    user_agent: str = "NewslyDocumentExtractor/1.0",
    request_headers: Mapping[str, str] | None = None,
) -> PublicFetch:
    """Fetch a bounded document, revalidating the public network on every redirect."""

    current_url = url
    timeout = httpx.Timeout(timeout_seconds)
    headers = {
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        "User-Agent": user_agent,
    }
    if request_headers:
        headers.update(request_headers)
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout,
        trust_env=False,
        headers=headers,
    ) as client:
        for redirect_count in range(max_redirects + 1):
            await require_public_url(current_url)
            try:
                async with client.stream("GET", current_url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise UrlSafetyError(
                                "Redirect response did not include a location",
                                code="fetch_failed",
                                retryable=False,
                                http_status=response.status_code,
                            )
                        if redirect_count >= max_redirects:
                            raise UrlSafetyError(
                                "Document exceeded the redirect limit",
                                code="fetch_failed",
                                retryable=False,
                                http_status=response.status_code,
                            )
                        current_url = urljoin(str(response.url), location)
                        continue

                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise UrlSafetyError(
                            f"Document fetch returned HTTP {response.status_code}",
                            code="fetch_failed",
                            retryable=response.status_code >= 500 or response.status_code == 429,
                            http_status=response.status_code,
                        ) from exc

                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_length = int(content_length)
                        except ValueError:
                            declared_length = None
                        if declared_length is not None and declared_length > max_bytes:
                            raise UrlSafetyError(
                                "Document exceeds the configured byte limit",
                                code="response_too_large",
                            )

                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise UrlSafetyError(
                                "Document exceeds the configured byte limit",
                                code="response_too_large",
                            )

                    final_url = str(response.url)
                    await require_public_url(final_url)
                    return PublicFetch(
                        final_url=final_url,
                        body=bytes(body),
                        content_type=response.headers.get("content-type"),
                    )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise UrlSafetyError(
                    "Document fetch failed before completion",
                    code="fetch_failed",
                    retryable=True,
                ) from exc

    raise UrlSafetyError("Document fetch redirect loop did not terminate", code="fetch_failed")


def decode_document(document: PublicFetch) -> str:
    """Decode HTTP bytes with a bounded, deterministic fallback order."""

    encoding = "utf-8"
    if document.content_type:
        for parameter in document.content_type.split(";")[1:]:
            name, separator, value = parameter.partition("=")
            if separator and name.strip().lower() == "charset" and value.strip():
                encoding = value.strip().strip('"')
                break
    try:
        return document.body.decode(encoding, errors="replace")
    except LookupError:
        return document.body.decode("utf-8", errors="replace")
