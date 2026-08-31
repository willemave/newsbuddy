import socket
from typing import Any

import httpx
import pytest

import newsly_document_extractor.url_safety as url_safety_module
from newsly_document_extractor.url_safety import (
    UrlSafetyError,
    fetch_public_document,
    require_public_url,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private",
        "http://[::1]/private",
        "http://[::ffff:127.0.0.1]/private",
        "http://169.254.169.254/latest/meta-data",
        "http://[ff0e::1]/multicast",
        "ftp://example.com/file",
        "https://user:secret@example.com/file",
        "https://example.com:99999/file",
    ],
)
async def test_private_or_unsafe_urls_are_rejected(url: str) -> None:
    with pytest.raises(UrlSafetyError):
        await require_public_url(url)


@pytest.mark.asyncio
async def test_mixed_public_and_private_dns_answers_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mixed_answers(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", mixed_answers)

    with pytest.raises(UrlSafetyError, match="non-public"):
        await require_public_url("https://mixed.example/article")


@pytest.mark.asyncio
async def test_static_fetch_revalidates_each_redirect_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validated: list[str] = []
    requests: list[str] = []

    async def validate(url: str) -> tuple[Any, ...]:
        validated.append(url)
        if "127.0.0.1" in url:
            raise UrlSafetyError("URL resolves to a non-public address")
        return ()

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    real_client = httpx.AsyncClient

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(url_safety_module, "require_public_url", validate)
    monkeypatch.setattr(url_safety_module.httpx, "AsyncClient", client_factory)

    with pytest.raises(UrlSafetyError, match="non-public"):
        await fetch_public_document(
            "https://example.com/start",
            max_bytes=65_536,
            timeout_seconds=1,
            max_redirects=2,
        )

    assert requests == ["https://example.com/start"]
    assert validated == ["https://example.com/start", "http://127.0.0.1/private"]


@pytest.mark.asyncio
async def test_static_fetch_bounds_streamed_response_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def validate(_url: str) -> tuple[Any, ...]:
        return ()

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 65_537)

    real_client = httpx.AsyncClient

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(url_safety_module, "require_public_url", validate)
    monkeypatch.setattr(url_safety_module.httpx, "AsyncClient", client_factory)

    with pytest.raises(UrlSafetyError, match="byte limit"):
        await fetch_public_document(
            "https://example.com/article",
            max_bytes=65_536,
            timeout_seconds=1,
            max_redirects=0,
        )
