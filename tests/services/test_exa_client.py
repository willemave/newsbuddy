from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.db import VendorUsageRecord
from app.services import exa_client


def test_get_exa_client_uses_canonical_http_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class BoundedClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(exa_client, "_exa_client", None)
    monkeypatch.setattr(exa_client, "_BoundedExa", BoundedClient)
    monkeypatch.setattr(
        exa_client,
        "get_settings",
        lambda: SimpleNamespace(exa_api_key="secret", http_timeout_seconds=30),
    )

    client = exa_client.get_exa_client()

    assert client is exa_client.get_exa_client()
    assert captured == {"api_key": "secret", "timeout_seconds": 30.0}


def test_exa_search_returns_empty_results_when_client_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exa_client, "get_exa_client", lambda: None)

    assert exa_client.exa_search("ai agents") == []


def test_exa_get_contents_raises_when_strict_mode_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exa_client, "get_exa_client", lambda: None)

    with pytest.raises(exa_client.ExaUnavailableError):
        exa_client.exa_get_contents(
            ["https://example.com/story"],
            raise_on_error=True,
        )


def test_exa_search_records_vendor_usage(
    db_session,
    vendor_usage_db,
    user_factory,
    monkeypatch,
) -> None:
    del vendor_usage_db
    user = user_factory()

    class DummyClient:
        def search(self, *_args, **_kwargs):
            return SimpleNamespace(
                results=[
                    SimpleNamespace(
                        title="Example",
                        url="https://example.com/story",
                        summary="Short summary",
                        text=None,
                        published_date="2026-04-14T00:00:00Z",
                    )
                ]
            )

    monkeypatch.setattr(exa_client, "get_exa_client", lambda: DummyClient())

    results = exa_client.exa_search(
        "ai agents",
        telemetry={
            "feature": "assistant",
            "operation": "assistant.search_web",
            "user_id": user.id,
        },
    )

    assert len(results) == 1
    row = db_session.query(VendorUsageRecord).one()
    assert row.provider == "exa"
    assert row.model == "search"
    assert row.user_id == user.id
    assert row.request_count == 1
    assert row.resource_count == 1


def test_exa_search_uses_bounded_transport_when_timeout_requested(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class ExistingClient:
        base_url = "https://api.exa.test"
        headers = {"User-Agent": "newsly-test"}

    class BoundedClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def search(self, *_args, **_kwargs):
            return SimpleNamespace(results=[])

    monkeypatch.setattr(exa_client, "get_exa_client", lambda: ExistingClient())
    monkeypatch.setattr(
        exa_client,
        "get_settings",
        lambda: SimpleNamespace(exa_api_key="secret", http_timeout_seconds=30),
    )
    monkeypatch.setattr(exa_client, "_BoundedExa", BoundedClient)
    monkeypatch.setattr(exa_client, "_record_exa_usage", lambda **_kwargs: None)

    assert exa_client.exa_search("bounded", request_timeout_seconds=1.25) == []
    assert captured == {
        "api_key": "secret",
        "base_url": "https://api.exa.test",
        "user_agent": "newsly-test",
        "timeout_seconds": 1.25,
    }


def test_exa_search_keeps_stricter_default_than_caller_timeout(monkeypatch) -> None:
    calls: list[str] = []

    class ExistingClient:
        def search(self, query: str, **_kwargs):
            calls.append(query)
            return SimpleNamespace(results=[])

    monkeypatch.setattr(exa_client, "get_exa_client", lambda: ExistingClient())
    monkeypatch.setattr(
        exa_client,
        "get_settings",
        lambda: SimpleNamespace(exa_api_key="secret", http_timeout_seconds=30),
    )
    monkeypatch.setattr(
        exa_client,
        "_BoundedExa",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must use stricter default")),
    )
    monkeypatch.setattr(exa_client, "_record_exa_usage", lambda **_kwargs: None)

    assert exa_client.exa_search("bounded", request_timeout_seconds=45) == []
    assert calls == ["bounded"]


def test_exa_get_contents_uses_tighter_caller_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class ExistingClient:
        base_url = "https://api.exa.test"
        headers = {"User-Agent": "newsly-test"}

    class BoundedClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def get_contents(self, *_args, **_kwargs):
            return SimpleNamespace(results=[])

    monkeypatch.setattr(exa_client, "get_exa_client", lambda: ExistingClient())
    monkeypatch.setattr(
        exa_client,
        "get_settings",
        lambda: SimpleNamespace(exa_api_key="secret", http_timeout_seconds=30),
    )
    monkeypatch.setattr(exa_client, "_BoundedExa", BoundedClient)
    monkeypatch.setattr(exa_client, "_record_exa_usage", lambda **_kwargs: None)

    assert (
        exa_client.exa_get_contents(
            ["https://example.com/story"],
            request_timeout_seconds=2.5,
        )
        == []
    )
    assert captured["timeout_seconds"] == 2.5


def test_exa_search_expired_timeout_does_not_initialize_client(monkeypatch) -> None:
    monkeypatch.setattr(
        exa_client,
        "get_exa_client",
        lambda: (_ for _ in ()).throw(AssertionError("client must not initialize")),
    )

    assert exa_client.exa_search("expired", request_timeout_seconds=0) == []


def test_bounded_exa_request_applies_http_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"results": []}

    class FakeHttpClient:
        def __init__(self, **kwargs) -> None:
            captured["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, **kwargs):
            captured["request"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(exa_client.httpx, "Client", FakeHttpClient)
    client = exa_client._BoundedExa(api_key="secret", timeout_seconds=0.75)

    assert client.request("/search", {"query": "test"}) == {"results": []}
    assert captured["client"] == {"timeout": 0.75, "follow_redirects": True}
    assert captured["request"]["url"] == "https://api.exa.ai/search"
    assert captured["request"]["json"] == {"query": "test"}
