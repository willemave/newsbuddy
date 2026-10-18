"""OpenRouter route, bill, and failure contracts without external calls."""

import asyncio
import copy
from pathlib import Path
from typing import Any

import httpx
import pytest

from newsly_evals.filtering import provider
from newsly_evals.filtering.artifacts import digest, write_json
from newsly_evals.filtering.ledger import read_jev_state
from newsly_evals.filtering.transport import OPENROUTER_PROVIDER, OPENROUTER_ROUTE


def raw_response() -> dict[str, Any]:
    return {
        "id": "gen-decision-fixture",
        "model": OPENROUTER_ROUTE["response_model"],
        "provider": "TypeSafe",
        "answers": {
            "filter": {
                "type": "choice",
                "choice": "match",
                "probabilities": {"match": 0.9, "no_match": 0.08, "insufficient_evidence": 0.02},
            }
        },
        "usage": {"input_tokens": 100, "output_tokens": 3, "cost": 0.0000042},
    }


def fixture_run(output: Path) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 2,
        "model": OPENROUTER_ROUTE["request_model"],
        "transport": copy.deepcopy(OPENROUTER_ROUTE),
        "allocation": {"request_cap": 16, "dollar_cap": 0.01},
        "jobs": [],
    }
    for index in range(2):
        request = {
            "model": body["model"],
            "provider": OPENROUTER_PROVIDER,
            "state": {"news": "Fixture", "filter": "Databases"},
            "questions": {},
        }
        body["jobs"].append(
            {"job_id": str(index), "request": request, "input_hash": digest(request)}
        )
    body["manifest_hash"] = digest(body)
    write_json(output / "judge-prompts.json", {"manifest_hash": body["manifest_hash"]})
    return body


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", "typesafe/jev-1.14"),
        ("model", "typesafe/jev-1.15-20261020"),
        ("provider", "Other"),
        ("id", ""),
        ("id", None),
    ],
)
def test_model_revision_and_provider_are_exact(field: str, value: Any) -> None:
    raw = raw_response()
    raw[field] = value
    with pytest.raises(ValueError):
        provider.validate_response(raw, str(OPENROUTER_ROUTE["request_model"]))


@pytest.mark.parametrize("cost", [None, True, -1, float("nan"), float("inf"), 0.01, 0])
def test_reported_cost_required_and_price_drift_rejected(cost: Any) -> None:
    raw = raw_response()
    raw["usage"]["cost"] = cost
    with pytest.raises(ValueError):
        provider.validate_response(raw, str(OPENROUTER_ROUTE["request_model"]))


def test_byok_rejected_and_small_float_rounding_allowed() -> None:
    raw = raw_response()
    raw["usage"]["is_byok"] = True
    with pytest.raises(ValueError, match="BYOK"):
        provider.validate_response(raw, str(OPENROUTER_ROUTE["request_model"]))
    raw["usage"]["is_byok"] = False
    raw["usage"]["cost"] += 1e-15
    assert provider.validate_response(raw, str(OPENROUTER_ROUTE["request_model"]))["provider"]


@pytest.mark.parametrize(
    "values,accepted",
    [
        ((0.40, 0.56, 0.03), True),
        ((0.40, 0.58, 0.03), True),
        ((0.40, 0.55, 0.03), False),
        ((0.405, 0.56, 0.03), False),
    ],
)
def test_only_cent_rounded_distributions_get_sum_tolerance(
    values: tuple[float, float, float], accepted: bool
) -> None:
    raw = raw_response()
    answer = raw["answers"]["filter"]
    answer["choice"] = "no_match"
    answer["probabilities"] = dict(zip(answer["probabilities"], values, strict=True))
    if accepted:
        validated = provider.validate_response(raw, str(OPENROUTER_ROUTE["request_model"]))
        assert validated["probabilities"] == answer["probabilities"]
    else:
        with pytest.raises(ValueError, match="sum to one"):
            provider.validate_response(raw, str(OPENROUTER_ROUTE["request_model"]))


@pytest.mark.parametrize("status", [402, 404, 503])
def test_routing_and_credit_errors_stop_dispatch(
    status: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = fixture_run(tmp_path)
    real_client = httpx.AsyncClient
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-key")
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(status))
        ),
    )
    result = asyncio.run(
        provider.run_jev(manifest, tmp_path, request_cap=16, dollar_cap=0.01, concurrency=1)
    )
    assert result["halted"] and result["attempts"] == 1
    assert result["cost_or_reserved_usd"] > 0


def test_duplicate_generation_halts_and_original_raw_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = fixture_run(tmp_path)
    real_client = httpx.AsyncClient
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-key")

    def send(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OPENROUTER_ROUTE["endpoint"]
        assert request.headers["Authorization"] == "Bearer test-only-key"
        return httpx.Response(200, json=raw_response())

    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(send)),
    )
    result = asyncio.run(
        provider.run_jev(manifest, tmp_path, request_cap=16, dollar_cap=0.01, concurrency=1)
    )
    assert result["halted"] and result["completed"] == 1 and result["attempts"] == 2
    state = read_jev_state(tmp_path, manifest)
    assert state["schema_version"] == 3
    assert len(list((tmp_path / "jev-raw").glob("*.json"))) == 2
    assert state["attempts"][0]["actual_cost"] == raw_response()["usage"]["cost"]
    assert "test-only-key" not in (tmp_path / "jev-ledger.jsonl").read_text()


def test_legacy_direct_run_and_changed_route_refuse_before_dispatch(tmp_path: Path) -> None:
    manifest = fixture_run(tmp_path)
    for changed in ({"schema_version": 1}, {"transport": {"endpoint": "https://other.invalid"}}):
        with pytest.raises(ValueError, match="pinned OpenRouter"):
            asyncio.run(
                provider.run_jev({**manifest, **changed}, tmp_path, request_cap=16, dollar_cap=0.01)
            )
    assert not (tmp_path / "jev-checkpoint.json").exists()


@pytest.mark.parametrize("always_invalid", [False, True])
def test_choice_mismatch_retries_are_bounded_and_billed(
    always_invalid: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = fixture_run(tmp_path)
    real_client = httpx.AsyncClient
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-key")
    calls = 0

    def send(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raw = raw_response()
        raw["id"] = f"gen-{calls}"
        if always_invalid or calls == 1:
            raw["answers"]["filter"]["choice"] = "no_match"
        return httpx.Response(200, json=raw)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(provider.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(send)),
    )
    result = asyncio.run(
        provider.run_jev(manifest, tmp_path, request_cap=16, dollar_cap=0.01, concurrency=1)
    )
    assert not result["halted"]
    assert result["attempts"] == (6 if always_invalid else 3)
    assert result["completed"] == (0 if always_invalid else 2)
    assert result["cost_or_reserved_usd"] == pytest.approx(calls * 0.0000042)
    state = read_jev_state(tmp_path, manifest)
    assert state["attempts"][0]["status"] == "invalid_choice"
    assert len(list((tmp_path / "jev-raw").glob("*.json"))) == calls
    resumed = asyncio.run(
        provider.run_jev(manifest, tmp_path, request_cap=16, dollar_cap=0.01, concurrency=1)
    )
    assert resumed["attempts"] == result["attempts"]
    assert calls == result["attempts"]


def test_choice_mismatch_does_not_mask_unknown_billing() -> None:
    raw = raw_response()
    raw["answers"]["filter"]["choice"] = "no_match"
    raw["usage"]["cost"] = None
    with pytest.raises(ValueError) as caught:
        provider.validate_response(raw, str(OPENROUTER_ROUTE["request_model"]))
    assert not isinstance(caught.value, provider.ChoiceMismatch)


@pytest.mark.parametrize("failure", ["duplicate", "over_reservation"])
def test_unsafe_mismatch_halts_but_remains_readable(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = fixture_run(tmp_path)
    real_client = httpx.AsyncClient
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-key")
    calls = 0

    def send(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raw = raw_response()
        if failure == "over_reservation":
            raw["answers"]["filter"]["choice"] = "no_match"
            raw["usage"]["input_tokens"] = 1_000_000
            raw["usage"]["cost"] = 0.042
        elif calls > 1:
            raw["answers"]["filter"]["choice"] = "no_match"
        return httpx.Response(200, json=raw)

    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(send)),
    )
    result = asyncio.run(
        provider.run_jev(manifest, tmp_path, request_cap=16, dollar_cap=0.01, concurrency=1)
    )
    assert result["halted"]
    state = read_jev_state(tmp_path, manifest)
    assert state["attempts"][-1]["status"] == "invalid_response"
    assert state["attempts"][-1]["halt"]
    assert state["attempts"][-1]["actual_cost"] > 0
    with pytest.raises(ValueError, match="Run halted"):
        asyncio.run(provider.run_jev(manifest, tmp_path, request_cap=16, dollar_cap=0.01))
