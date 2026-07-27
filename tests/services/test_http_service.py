from typing import Any, Literal, cast

import httpx
import pytest

from app.services.http import (
    HttpService,
    NonRetryableError,
    get_http_service,
    reset_http_service_for_testing,
)


class _ExplodingClient:
    def __init__(self) -> None:
        self.calls = 0

    def __enter__(self) -> "_ExplodingClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:  # noqa: ANN001
        return False

    def get(self, url: str, headers: dict[str, str]) -> None:
        del url, headers
        self.calls += 1
        raise httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known")


class _SequenceClient:
    def __init__(self, outcomes: list[int | Exception]) -> None:
        self.outcomes = iter(outcomes)
        self.calls = 0

    def get(self, url: str, headers: dict[str, str]) -> httpx.Response:
        del headers
        self.calls += 1
        request = httpx.Request("GET", url)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, request=request)


def test_fetch_treats_dns_resolution_error_as_non_retryable(monkeypatch) -> None:
    service = HttpService()
    client = _ExplodingClient()

    monkeypatch.setattr(service, "get_client", lambda url=None: client)

    with pytest.raises(NonRetryableError, match="DNS resolution error"):
        service.fetch("https://www.thisweekinmachinelearning.com/")

    assert client.calls == 1


def test_fetch_retries_transient_server_errors(monkeypatch) -> None:
    service = HttpService()
    client = _SequenceClient([503, 503, 200])
    monkeypatch.setattr(service, "get_client", lambda url=None: client)
    monkeypatch.setattr(cast(Any, service.fetch).retry, "sleep", lambda _delay: None)

    response = service.fetch("https://example.com/feed.xml")

    assert response.status_code == 200
    assert client.calls == 3


def test_fetch_does_not_retry_client_errors(monkeypatch) -> None:
    service = HttpService()
    client = _SequenceClient([404, 200])
    monkeypatch.setattr(service, "get_client", lambda url=None: client)
    monkeypatch.setattr(cast(Any, service.fetch).retry, "sleep", lambda _delay: None)

    with pytest.raises(NonRetryableError, match="Non-retryable HTTP 404"):
        service.fetch("https://example.com/missing.xml")

    assert client.calls == 1


def test_fetch_retries_timeouts(monkeypatch) -> None:
    url = "https://example.com/feed.xml"
    request = httpx.Request("GET", url)
    service = HttpService()
    client = _SequenceClient(
        [
            httpx.ReadTimeout("timed out", request=request),
            httpx.ReadTimeout("timed out", request=request),
            200,
        ]
    )
    monkeypatch.setattr(service, "get_client", lambda url=None: client)
    monkeypatch.setattr(cast(Any, service.fetch).retry, "sleep", lambda _delay: None)

    assert service.fetch(url).status_code == 200
    assert client.calls == 3


def test_clients_are_reused_by_ssl_policy_and_closed() -> None:
    service = HttpService()

    normal_client = service.get_client("https://example.com/one")
    assert service.get_client("https://another.example/two") is normal_client

    relaxed_client = service.get_client("https://feeds.0x80.pl/rss")
    assert relaxed_client is not normal_client
    assert service.get_client("https://0x80.pl/atom") is relaxed_client

    service.close()

    assert normal_client.is_closed
    assert relaxed_client.is_closed


def test_process_service_can_be_reset_between_tests() -> None:
    reset_http_service_for_testing()
    first = get_http_service()

    reset_http_service_for_testing()
    second = get_http_service()

    try:
        assert second is not first
    finally:
        reset_http_service_for_testing()
