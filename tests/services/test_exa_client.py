from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.db import VendorUsageRecord
from app.services import exa_client


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


def test_exa_search_records_vendor_usage(db_session, vendor_usage_db, monkeypatch) -> None:
    del vendor_usage_db

    class DummyClient:
        def search_and_contents(self, *_args, **_kwargs):
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
            "user_id": 7,
        },
    )

    assert len(results) == 1
    row = db_session.query(VendorUsageRecord).one()
    assert row.provider == "exa"
    assert row.model == "search"
    assert row.user_id == 7
    assert row.request_count == 1
    assert row.resource_count == 1
