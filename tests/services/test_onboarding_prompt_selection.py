from __future__ import annotations

from app.services.onboarding.internal_models import _DiscoveryWebResult
from app.services.onboarding.query_heuristics import _select_prompt_results


def test_select_prompt_results_balances_lanes(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.onboarding.query_heuristics.DISCOVERY_PROMPT_MAX_WEB_RESULTS",
        4,
    )
    results = [
        _DiscoveryWebResult(title="A1", url="https://a1.example", lane_name="lane-a"),
        _DiscoveryWebResult(title="A2", url="https://a2.example", lane_name="lane-a"),
        _DiscoveryWebResult(title="B1", url="https://b1.example", lane_name="lane-b"),
        _DiscoveryWebResult(title="B2", url="https://b2.example", lane_name="lane-b"),
    ]

    selected = _select_prompt_results(results, lane_balanced=True)

    assert [item.title for item in selected] == ["A1", "B1", "A2", "B2"]


def test_select_prompt_results_dedupes_urls() -> None:
    results = [
        _DiscoveryWebResult(title="First", url="https://dup.example"),
        _DiscoveryWebResult(title="Duplicate", url="https://dup.example"),
        _DiscoveryWebResult(title="Unique", url="https://unique.example"),
    ]

    selected = _select_prompt_results(results)

    assert [item.title for item in selected] == ["First", "Unique"]
