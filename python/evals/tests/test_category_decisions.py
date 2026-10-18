"""Bounded category inference contracts; all HTTP responses are local fixtures."""

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from newsly_evals.filtering.transport import OPENROUTER_PROVIDER, OPENROUTER_ROUTE

spec = importlib.util.spec_from_file_location(
    "category_decisions", Path(__file__).parents[1] / "scripts/run_category_decisions.py"
)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def request() -> dict[str, Any]:
    return {
        "model": OPENROUTER_ROUTE["request_model"],
        "provider": OPENROUTER_PROVIDER,
        "state": {"news": "Fixture"},
        "questions": {"category": {"type": "choice", "criteria": {"a": "A", "b": "B"}}},
    }


def response(index: int = 1) -> dict[str, Any]:
    return {
        "id": f"gen-{index}",
        "model": OPENROUTER_ROUTE["response_model"],
        "provider": "TypeSafe",
        "usage": {"input_tokens": 100, "output_tokens": 2, "cost": 0.0000042},
        "answers": {
            "category": {
                "type": "choice",
                "choice": "a",
                "probabilities": {"a": 0.9, "b": 0.1},
            }
        },
    }


def manifest(tmp_path: Path, count: int = 5, dollars: float = 0.1) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "request_cap": 300,
                "dollar_cap": dollars,
                "jobs": [{"job_id": str(i), "request": request()} for i in range(count)],
            }
        )
    )
    return path


@pytest.mark.parametrize(
    "mutation",
    [
        "model",
        "provider",
        "id",
        "keys",
        "bool",
        "nan",
        "sum",
        "choice",
        "type",
        "cost",
    ],
)
def test_response_validation(mutation: str) -> None:
    raw = response()
    answer = raw["answers"]["category"]
    if mutation in {"model", "provider", "id"}:
        raw[mutation] = ""
    elif mutation == "keys":
        answer["probabilities"]["c"] = answer["probabilities"].pop("b")
    elif mutation == "bool":
        answer["probabilities"]["b"] = True
    elif mutation == "nan":
        answer["probabilities"]["b"] = float("nan")
    elif mutation == "sum":
        answer["probabilities"]["b"] = 0.01
    elif mutation == "choice":
        answer["choice"] = "b"
    elif mutation == "type":
        answer["type"] = "text"
    else:
        raw["usage"]["cost"] = None
    with pytest.raises(ValueError):
        runner.validate_response(raw, request())


def test_cent_rounding_tolerance() -> None:
    raw = response()
    raw["answers"]["category"]["probabilities"] = {"a": 0.9, "b": 0.09}
    runner.validate_response(raw, request())
    raw["answers"]["category"]["probabilities"] = {"a": 0.901, "b": 0.09}
    with pytest.raises(ValueError, match="normalized"):
        runner.validate_response(raw, request())


def test_resume_counts_failures_and_never_retries(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-fixture")
    path = manifest(tmp_path)
    output = tmp_path / "output"
    calls = 0

    def send(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert (output / "attempts.jsonl").read_text().count('"event":"reserve"') == calls
        raw = response(calls)
        if calls == 2:
            raw["answers"]["category"]["choice"] = "b"
        return httpx.Response(200, json=raw)

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            first = await runner.run(path, output, 4, client=client)
            assert first["attempts"] == 4 and first["failed"] == 1
            assert first["cost_or_reserved_usd"] == pytest.approx(4 * 0.0000042)
            resumed = await runner.run(path, output, 300, 4, client=client)
            assert resumed["attempts"] == 5 and resumed["completed"] == 4
            await runner.run(path, output, 300, 4, client=client)

    asyncio.run(exercise())
    assert calls == 5
    assert len(list((output / "raw").glob("*.json"))) == 5
    assert "secret-fixture" not in (output / "attempts.jsonl").read_text()
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(runner.run(path, output))


@pytest.mark.parametrize("failure", ["unknown_cost", "duplicate", "http", "transport", "error"])
def test_failure_retention_and_billing(
    tmp_path: Path,
    monkeypatch: Any,
    failure: str,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-fixture")
    path = manifest(tmp_path, count=2)
    output = tmp_path / "out"
    calls = 0

    def send(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raw = response(1 if failure == "duplicate" else calls)
        if failure == "transport":
            raise httpx.ReadTimeout("uncertain", request=req)
        if failure == "unknown_cost":
            raw["usage"]["cost"] = None
        if failure == "error":
            raw["error"] = {"message": "failed with usage"}
        return httpx.Response(503 if failure == "http" else 200, json=raw)

    async def exercise() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            result = await runner.run(path, output, client=client)
            await runner.run(path, output, client=client)
            return result

    result = asyncio.run(exercise())
    assert result["failed"] >= 1
    assert calls == result["attempts"]
    if failure in {"unknown_cost", "transport"}:
        assert result["cost_or_reserved_usd"] > 0.0000042
    else:
        assert result["cost_or_reserved_usd"] == pytest.approx(calls * 0.0000042)
    if failure != "transport":
        assert len(list((output / "raw").glob("*.json"))) == calls


def test_pending_is_reserved_and_skipped(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-fixture")
    path = manifest(tmp_path, count=1)
    output = tmp_path / "out"

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response()))
        ) as client:
            await runner.run(path, output, client=client)
            ledger = output / "attempts.jsonl"
            ledger.write_text(ledger.read_text().splitlines()[0] + "\n")
            result = await runner.run(path, output, client=client)
            assert result["pending"] == 1 and result["completed"] == 0
            assert result["cost_or_reserved_usd"] > 0.0000042

    asyncio.run(exercise())


def test_spend_cap_before_dispatch(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-fixture")
    path = manifest(tmp_path, dollars=0.000001)

    def forbidden(_: httpx.Request) -> httpx.Response:
        raise AssertionError("must not dispatch")

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            result = await runner.run(path, tmp_path / "out", client=client)
            assert result["attempts"] == 0

    asyncio.run(exercise())


def test_concurrent_dispatch_reserves_whole_batch(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-fixture")
    path = manifest(tmp_path, count=4)
    output = tmp_path / "out"
    active = 0
    peak = 0
    calls = 0

    async def send(_: httpx.Request) -> httpx.Response:
        nonlocal active, peak, calls
        calls += 1
        index = calls
        active += 1
        peak = max(active, peak)
        assert (output / "attempts.jsonl").read_text().count('"event":"reserve"') == 4
        await asyncio.sleep(0.001)
        active -= 1
        return httpx.Response(200, json=response(index))

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            result = await runner.run(path, output, 4, 4, client=client)
            assert result["completed"] == 4

    asyncio.run(exercise())
    assert peak == 4


def test_failed_response_overbilling_is_metered_and_halts(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-fixture")
    path = manifest(tmp_path)
    raw = response()
    raw["error"] = {"message": "failed"}
    raw["usage"]["cost"] = 0.02

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=raw))
        ) as client:
            result = await runner.run(path, tmp_path / "out", client=client)
            assert result["attempts"] == 1 and result["halted"]
            assert result["cost_or_reserved_usd"] == 0.02

    asyncio.run(exercise())


def test_malformed_raw_is_preserved(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-fixture")
    path = manifest(tmp_path)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"not json"))
        ) as client:
            result = await runner.run(path, tmp_path / "out", client=client)
            assert result["failed"] == 1 and result["halted"]

    asyncio.run(exercise())
    assert (tmp_path / "out/raw/0001.json").read_bytes() == b"not json"


@pytest.mark.parametrize("field", ["model", "provider"])
def test_route_mismatch_halts_and_remains_billed(
    tmp_path: Path,
    monkeypatch: Any,
    field: str,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-fixture")
    path = manifest(tmp_path)
    output = tmp_path / "out"
    calls = 0

    def send(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raw = response(calls)
        raw[field] = "unexpected-route"
        return httpx.Response(200, json=raw)

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            result = await runner.run(path, output, client=client)
            assert result["attempts"] == result["failed"] == 1
            assert result["halted"]
            assert result["cost_or_reserved_usd"] == pytest.approx(0.0000042)
            resumed = await runner.run(path, output, client=client)
            assert resumed == pytest.approx(result)

    asyncio.run(exercise())
    assert calls == 1
    result_row = json.loads((output / "results.jsonl").read_text())
    assert result_row["error"] == f"wrong_{field}"
    assert result_row["halt"]
