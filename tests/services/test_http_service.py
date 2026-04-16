from typing import Literal

import httpx
import pytest

from app.services.http import HttpService, NonRetryableError


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


def test_fetch_treats_dns_resolution_error_as_non_retryable(monkeypatch) -> None:
    service = HttpService()
    client = _ExplodingClient()

    monkeypatch.setattr(service, "get_client", lambda url=None: client)

    with pytest.raises(NonRetryableError, match="DNS resolution error"):
        service.fetch("https://www.thisweekinmachinelearning.com/")

    assert client.calls == 1
