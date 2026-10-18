"""Offline contract, budget, leakage and precision/recall checks."""

import asyncio
import copy
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from newsly_evals.filtering import judge, provider
from newsly_evals.filtering.artifacts import (
    MODEL,
    VARIANTS,
    digest,
    load_manifest,
    prepare,
    write_json,
    write_jsonl,
)
from newsly_evals.filtering.judge import JUDGE_MODEL, batches, validate_labels
from newsly_evals.filtering.ledger import (
    append_event,
    initialize_ledger,
    persist_raw,
    read_jev_state,
)
from newsly_evals.filtering.metrics import (
    confidence_intervals,
    grouped,
    score,
    select_development,
    summarize,
)
from newsly_evals.filtering.references import accepted_references
from newsly_evals.filtering.transport import OPENROUTER_PROVIDER, OPENROUTER_ROUTE


@pytest.fixture(autouse=True)
def isolated_judge_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Offline judge fakes must not inherit a developer's paid-provider credentials.
    # Guard tests explicitly set the key when testing rejection.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def source(index: int, split: str = "development") -> dict[str, Any]:
    return {
        "news_id": index,
        "title": "New database engine",
        "summary": "A faster database.",
        "key_points": ["Indexes improved."],
        "summary_hash": digest(index),
        "aggregator": "hackernews",
        "publisher_domain": "example.com",
        "age_slice": "recent",
        "split": split,
        "story_group_id": str(index),
    }


@pytest.fixture
def manifest(tmp_path: Path) -> dict[str, Any]:
    rows = [source(i, "development" if i < 8 else "test") for i in range(16)]
    write_jsonl(tmp_path / "sample.jsonl", rows)
    profiles = [
        {
            "id": name,
            "canonical": {
                "interests": ["databases"],
                "exclusions": [],
                "match_rule": "any_interest_unless_excluded",
            },
            "variants": {v: v + ": databases" for v in VARIANTS},
        }
        for name in ("software_security", "ai_systems", "sports")
    ]
    write_json(tmp_path / "profiles.json", profiles)
    prepared = prepare(tmp_path / "sample.jsonl", tmp_path / "profiles.json", tmp_path / "run")
    judge.prepare_judge(prepared, tmp_path / "run")
    return prepared


def response(choice: str = "match", *, routed: bool = False) -> dict[str, Any]:
    raw = {
        "model": MODEL,
        "answers": {
            "filter": {
                "type": "choice",
                "choice": choice,
                "probabilities": {
                    label: float(label == choice)
                    for label in ("match", "no_match", "insufficient_evidence")
                },
            }
        },
        "usage": {"input_tokens": 100, "output_tokens": 3},
    }

    if routed:
        raw.update(
            {
                "model": OPENROUTER_ROUTE["response_model"],
                "provider": "TypeSafe",
                "id": str(uuid.uuid4()),
            }
        )
        raw["usage"]["cost"] = 100 * provider.INPUT_RATE
    return raw


def routed_manifest(manifest: dict[str, Any], tmp_path: Path, request_cap: int) -> dict[str, Any]:
    value = copy.deepcopy(manifest)
    value.update(
        {
            "schema_version": 2,
            "model": OPENROUTER_ROUTE["request_model"],
            "transport": OPENROUTER_ROUTE,
            "allocation": {"request_cap": request_cap, "dollar_cap": 5},
        }
    )
    for item in value["jobs"]:
        item["request"].update({"model": value["model"], "provider": OPENROUTER_PROVIDER})
        item["input_hash"] = digest(item["request"])
    value["manifest_hash"] = digest({k: v for k, v in value.items() if k != "manifest_hash"})
    write_json(
        tmp_path / "run" / "judge-prompts.json",
        {
            "manifest_hash": value["manifest_hash"],
            "batches": batches(value),
            "audit_batches": batches(value, audit=True),
        },
    )
    return value


def test_manifest_frozen_and_distinct_profiles(manifest: dict[str, Any], tmp_path: Path) -> None:
    assert len(manifest["jobs"]) == 128
    assert len(manifest["pairs"]) == 32
    human_rows = [
        json.loads(line)
        for line in (tmp_path / "run" / "human-review.jsonl").read_text().splitlines()
    ]
    assert all(
        "pairing_role" not in row and row["review_selection"] == "unselected" for row in human_rows
    )
    for news_id in range(16):
        pairs = [p for p in manifest["pairs"] if p["news_id"] == news_id]
        assert len({p["profile_id"] for p in pairs}) == 2
        assert {p["pairing_role"] for p in pairs} == {"affinity", "balanced_control"}
    repeated = prepare(tmp_path / "sample.jsonl", tmp_path / "profiles.json", tmp_path / "run")
    assert repeated == manifest
    edited = json.loads((tmp_path / "profiles.json").read_text())
    edited[0]["variants"]["prose"] = "changed"
    write_json(tmp_path / "profiles.json", edited)
    with pytest.raises(ValueError, match="different frozen"):
        prepare(tmp_path / "sample.jsonl", tmp_path / "profiles.json", tmp_path / "run")


def test_story_leakage_and_tampering(manifest: dict[str, Any], tmp_path: Path) -> None:
    rows = [source(1), source(2, "test")]
    rows[1]["story_group_id"] = "1"
    write_jsonl(tmp_path / "sample.jsonl", rows)
    with pytest.raises(ValueError, match="boundary"):
        prepare(tmp_path / "sample.jsonl", tmp_path / "profiles.json", tmp_path / "other")
    manifest["pairs"][0]["summary_hash"] = "changed"
    write_json(tmp_path / "tampered.json", manifest)
    with pytest.raises(ValueError, match="integrity"):
        load_manifest(tmp_path / "tampered.json")


@pytest.mark.parametrize("mutation", ["nan", "sum", "argmax", "usage", "model", "bool"])
def test_invalid_provider_contract(mutation: str) -> None:
    raw = response()
    if mutation == "nan":
        raw["answers"]["filter"]["probabilities"]["match"] = float("nan")
    elif mutation == "sum":
        raw["answers"]["filter"]["probabilities"]["match"] = 0.2
    elif mutation == "argmax":
        raw["answers"]["filter"]["choice"] = "no_match"
    elif mutation == "usage":
        del raw["usage"]
    elif mutation == "model":
        raw["model"] = "jev-latest"
    else:
        raw["usage"]["input_tokens"] = True
    with pytest.raises((ValueError, KeyError)):
        provider.validate_response(raw, MODEL)


def test_provider_resume_and_budget(
    manifest: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = routed_manifest(manifest, tmp_path, 3)
    original_client = httpx.AsyncClient
    calls = []

    def send(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=response(routed=True))

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(send)),
    )
    output = tmp_path / "run"
    first = asyncio.run(provider.run_jev(manifest, output, limit=2, request_cap=3, dollar_cap=5))
    assert first["completed"] == 2
    second = asyncio.run(provider.run_jev(manifest, output, limit=2, request_cap=3, dollar_cap=5))
    assert second["completed"] == 3
    assert len(calls) == 3
    assert "test-secret" not in (output / "jev-checkpoint.json").read_text()
    with pytest.raises(ValueError, match="frozen run allocation"):
        asyncio.run(provider.run_jev(manifest, output, request_cap=4, dollar_cap=5))
    manifest = copy.deepcopy(manifest)
    manifest["allocation"] = {"request_cap": 8400, "dollar_cap": 1e-12}
    judge.prepare_judge(manifest, tmp_path / "tiny")
    zero = asyncio.run(
        provider.run_jev(manifest, tmp_path / "tiny", dollar_cap=1e-12, request_cap=8400)
    )
    assert zero["attempts"] == 0


def test_invalid_usage_stops_new_calls(
    manifest: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = routed_manifest(manifest, tmp_path, 8400)
    original_client = httpx.AsyncClient
    raw = response(routed=True)
    del raw["usage"]
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=raw))
        ),
    )
    result = asyncio.run(
        provider.run_jev(manifest, tmp_path / "run", concurrency=1, dollar_cap=5, request_cap=8400)
    )
    assert result["halted"] and result["attempts"] == 1
    with pytest.raises(ValueError, match="halted"):
        asyncio.run(provider.run_jev(manifest, tmp_path / "run", dollar_cap=5, request_cap=8400))


def test_judge_blinding_and_exact_coverage(manifest: dict[str, Any]) -> None:
    batch = batches(manifest)[0]
    assert '"variants"' not in batch["prompt"]
    assert '"prose"' not in batch["prompt"]
    pairs = {p["pair_id"]: p for p in manifest["pairs"]}
    labels = [
        {
            "pair_id": pair_id,
            "label": "match",
            "reason": "Database research.",
            "evidence": "A faster database.",
            "decisive_clause": "interest_1",
        }
        for pair_id in batch["pair_ids"]
    ]
    raw: dict[str, Any] = {"modelUsage": {JUDGE_MODEL: {}}, "structured_output": {"labels": labels}}
    assert len(validate_labels(raw, batch, pairs)) == len(labels)
    raw["modelUsage"]["claude-haiku-5-5"] = {}
    with pytest.raises(ValueError, match="exclusively"):
        validate_labels(raw, batch, pairs)
    del raw["modelUsage"]["claude-haiku-5-5"]
    labels[-1] = labels[0]
    with pytest.raises(ValueError, match="duplicate"):
        validate_labels(raw, batch, pairs)


def row(gold: str, choice: str | None) -> dict[str, Any]:
    return {
        "gold": gold,
        "result": None if choice is None else provider.validate_response(response(choice), MODEL),
        "story_group": gold + str(choice),
    }


def test_failopen_and_unknown_denominators() -> None:
    metrics = summarize(
        [
            row("match", "match"),
            row("match", "no_match"),
            row("no_match", "no_match"),
            row("no_match", None),
            row("insufficient_evidence", "match"),
        ],
        0.9,
    )
    assert metrics["admitted_precision"] == 0.5
    assert metrics["relevance_recall"] == 0.5
    assert metrics["exclusion_precision"] == 0.5
    assert metrics["unresolved_gold"] == 1 and metrics["unavailable"] == 1
    identical = {
        variant: [row("match", "match"), row("no_match", "no_match")] for variant in VARIANTS
    }
    ci = confidence_intervals(identical, "prose", 0.9, draws=25)
    assert ci["intervals_95_percent"]["admitted_precision_delta_vs_bullets"]["low"] == 0


def test_selection_is_development_only(manifest: dict[str, Any], tmp_path: Path) -> None:
    output = tmp_path / "run"
    labels = [
        {
            "pair_id": pair["pair_id"],
            "label": "match" if pair["news_id"] % 2 else "no_match",
            "reason": "Fixture",
            "evidence": "A faster database.",
            "decisive_clause": "interest_1",
        }
        for pair in manifest["pairs"]
    ]
    bind_labels(manifest, output, labels)
    results = {}
    pair_by_id = {p["pair_id"]: p for p in manifest["pairs"]}
    for job in manifest["jobs"]:
        pair = pair_by_id[job["pair_id"]]
        gold = "match" if pair["news_id"] % 2 else "no_match"
        choice = gold if job["variant"] == "bullets" else "match"
        results[job["job_id"]] = {
            **provider.validate_response(response(choice), MODEL),
            "input_hash": job["input_hash"],
            "raw_response": response(choice),
        }
    write_jev_results(manifest, output, results)
    before = score(manifest, output)
    assert before["selection"]["variant"] == "bullets"
    for label in labels:
        if pair_by_id[label["pair_id"]]["split"] == "test":
            label["label"] = "no_match" if label["label"] == "match" else "match"
    bind_labels(manifest, output, labels)
    after = score(manifest, output)
    assert after["selection"] == before["selection"]


def bind_labels(manifest: dict[str, Any], output: Path, labels: list[dict[str, Any]]) -> None:
    write_jsonl(output / "judge-labels.jsonl", labels)
    by_id = {row["pair_id"]: row for row in labels}
    attempts = []
    results = {}
    for index, batch in enumerate(batches(manifest)):
        batch_labels = [by_id[key] for key in batch["pair_ids"]]
        raw = {"modelUsage": {JUDGE_MODEL: {}}, "structured_output": {"labels": batch_labels}}
        raw_path = "judge-raw/" + batch["batch_id"] + ".json"
        write_json(output / raw_path, raw)
        attempts.append(
            {
                "batch_id": batch["batch_id"],
                "status": "complete",
                "audit": False,
                "attempt_index": index,
                "raw_path": raw_path,
                "raw_hash": digest(raw),
            }
        )
        results[batch["batch_id"]] = {"model_usage": raw["modelUsage"], "labels": batch_labels}
    write_json(
        output / "judge-checkpoint.json",
        {"manifest_hash": manifest["manifest_hash"], "attempts": attempts, "results": results},
    )


def test_judge_resume_and_repeat_share_call_budget(
    manifest: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def call(batch: dict[str, Any]) -> dict[str, Any]:
        return {
            "modelUsage": {JUDGE_MODEL: {}},
            "structured_output": {
                "labels": [
                    {
                        "pair_id": pair_id,
                        "label": "match",
                        "reason": "Database research.",
                        "evidence": "A faster database.",
                        "decisive_clause": "interest_1",
                    }
                    for pair_id in batch["pair_ids"]
                ]
            },
        }

    monkeypatch.setattr(judge, "call_judge", call)
    output = tmp_path / "run"
    first = judge.run_judge(manifest, output, max_calls=3, limit=1)
    assert first["labels"] == 16 and first["calls"] == 1
    repeated = judge.run_judge(manifest, output, max_calls=3, limit=1)
    assert repeated["calls"] == 2
    audit = judge.run_judge(manifest, output, max_calls=3, audit=True)
    assert audit["repeat_labels"] == 3 and audit["calls"] == 3
    report = json.loads((output / "judge-repeat-audit.json").read_text())
    assert report["disagreement_rate"] == 0


def test_http_auth_error_stops_batch(
    manifest: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = routed_manifest(manifest, tmp_path, 8400)
    original_client = httpx.AsyncClient
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(401))
        ),
    )
    result = asyncio.run(
        provider.run_jev(manifest, tmp_path / "run", concurrency=1, dollar_cap=5, request_cap=8400)
    )
    assert result["halted"] and result["attempts"] == 1


def test_no_selection_on_partial_and_judge_provenance(
    manifest: dict[str, Any], tmp_path: Path
) -> None:
    output = tmp_path / "run"
    labels = [
        {
            "pair_id": pair["pair_id"],
            "label": "match",
            "reason": "Fixture",
            "evidence": "A faster database.",
            "decisive_clause": "interest_1",
        }
        for pair in manifest["pairs"]
    ]
    bind_labels(manifest, output, labels)
    write_jev_results(manifest, output, {})
    report = score(manifest, output, allow_partial=True)
    assert report["provisional"] and report["selection"] is None
    assert report["heldout_metrics_withheld"]
    assert report["selected_test"] == {}
    assert set(report["accept_all_baseline"]) == {"development"}
    for section in ("fixed_curves", "completed_only_curves", "fixed_0_9_subgroups"):
        assert all(set(splits) == {"development"} for splits in report[section].values())
    write_jsonl(
        output / "human-labels.jsonl",
        [
            {
                "pair_id": labels[0]["pair_id"],
                "manifest_hash": manifest["manifest_hash"],
                "human_label": "no_match",
                "review_cohort": "random_audit",
            },
            {
                "pair_id": labels[1]["pair_id"],
                "manifest_hash": manifest["manifest_hash"],
                "human_label": "match",
                "review_cohort": "profile_coverage",
            },
        ],
    )
    sensitivity = score(manifest, output, allow_partial=True)["human_sensitivity"]
    assert set(sensitivity["cohorts"]) == {"random_audit", "profile_coverage"}
    assert sensitivity["cohorts"]["random_audit"]["selection_type"] == "random"
    assert sensitivity["cohorts"]["profile_coverage"]["selection_type"] == "coverage"
    for section in ("fixed_curves", "completed_only_curves", "fixed_0_9_subgroups"):
        for cohort in sensitivity["cohorts"].values():
            for analysis in ("human_subset_only", "reference_override_sensitivity"):
                assert all(
                    set(splits) == {"development"} for splits in cohort[analysis][section].values()
                )
    assert not (output / "selection.json").exists()
    labels[0]["label"] = "no_match"
    write_jsonl(output / "judge-labels.jsonl", labels)
    with pytest.raises(ValueError, match="manifest-bound"):
        score(manifest, output, allow_partial=True)


def test_judge_batches_never_repeat_article(manifest: dict[str, Any]) -> None:
    by_id = {p["pair_id"]: p for p in manifest["pairs"]}
    for audit in (False, True):
        for batch in batches(manifest, audit=audit):
            ids = [by_id[pair_id]["news_id"] for pair_id in batch["pair_ids"]]
            assert len(set(ids)) == len(ids)


def test_profile_recall_gate_prevents_majority_masking() -> None:
    rows_by_variant = {}
    for variant in VARIANTS:
        rows = []
        for index in range(1120):
            profile = "small" if index < 20 else "large"
            gold = "no_match" if index >= 1020 else "match"
            choice = (
                "no_match" if variant == "bullets" and (index < 2 or index >= 1020) else "match"
            )
            rows.append(
                {
                    **row(gold, choice),
                    "profile_id": profile,
                    "split": "development",
                    "age_slice": "recent",
                }
            )
        rows_by_variant[variant] = rows
    selected = select_development(rows_by_variant)
    assert selected is not None and selected["variant"] == "prose"


def test_judge_halt_recovery_preserves_attempts_and_raw(
    manifest: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    def call(batch: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return {
            "modelUsage": {JUDGE_MODEL: {}},
            "structured_output": {
                "labels": [
                    {
                        "pair_id": pair_id,
                        "label": "match",
                        "reason": "Database research.",
                        "evidence": "A faster ... database."
                        if call_count == 1
                        else "A faster database.",
                        "decisive_clause": "interest_1",
                    }
                    for pair_id in batch["pair_ids"]
                ]
            },
        }

    monkeypatch.setattr(judge, "call_judge", call)
    output = tmp_path / "run"
    first = judge.run_judge(manifest, output, max_calls=3, limit=1, concurrency=1)
    assert first["halted"] and first["calls"] == 1
    with pytest.raises(ValueError, match="acknowledge-halt"):
        judge.run_judge(manifest, output, max_calls=3, limit=1)
    batch_id = batches(manifest)[0]["batch_id"]
    recovered = judge.run_judge(manifest, output, max_calls=3, limit=1, acknowledge_halt=[batch_id])
    assert not recovered["halted"] and recovered["calls"] == 2
    state = json.loads((output / "judge-checkpoint.json").read_text())
    assert state["attempts"][0]["halt"]
    assert state["halt_acknowledgements"][0]["attempt_indices"] == [0]
    assert len(list((output / "judge-raw").glob("*.json"))) == 2
    with pytest.raises(ValueError, match="outstanding halted"):
        judge.run_judge(manifest, output, max_calls=3, acknowledge_halt=[batch_id])


def test_judge_environment_blocks_paid_key_and_removes_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://invalid.example")
    monkeypatch.setenv("CLAUDECODE", "nested")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "secret")
    clean = judge.judge_environment()
    assert not any(key.startswith(("ANTHROPIC_", "CLAUDE")) for key in clean)
    assert "PATH" in clean
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(ValueError, match="Refusing exported"):
        judge.judge_environment()


def write_jev_results(manifest: dict[str, Any], output: Path, results: dict[str, Any]) -> None:
    initialize_ledger(output, manifest, 5, 8400)
    for index, (job_id, result) in enumerate(results.items()):
        attempt = {
            "attempt_index": index,
            "job_id": job_id,
            "input_hash": result["input_hash"],
            "reserved_cost": 1000 * provider.INPUT_RATE,
            "reserved_tokens": 1000,
            "status": "reserved",
            "number": 0,
        }
        append_event(output, {"event": "reserve", "attempt_index": index, "attempt": attempt})
        raw = result.pop("raw_response")
        raw_metadata = persist_raw(output, attempt, raw)
        append_event(
            output,
            {
                "event": "finish",
                "attempt_index": index,
                "outcome": {
                    "status": "complete",
                    **raw_metadata,
                    "actual_cost": raw["usage"]["input_tokens"] * provider.INPUT_RATE,
                },
                "result": result,
            },
        )


def test_macro_precision_counts_profiles_with_no_admissions() -> None:
    rows = [
        {**row("match", "no_match"), "profile_id": "empty"},
        {**row("match", "match"), "profile_id": "retained"},
    ]
    summary = grouped(rows, 0.9, "profile_id")
    assert summary["groups"]["empty"]["admitted_precision"] is None
    assert summary["macro"]["admitted_precision"] == 0.5
    assert summary["macro"]["admitted_precision_profiles_in_denominator"] == 2


@pytest.mark.parametrize("status", [429, 529])
def test_retry_accounting_retains_unknown_charges(
    manifest: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    manifest = routed_manifest(manifest, tmp_path, 3)
    original_client = httpx.AsyncClient
    calls = 0

    def send(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return (
            httpx.Response(status, headers={"Retry-After": "0"})
            if calls < 3
            else httpx.Response(200, json=response(routed=True))
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(send)),
    )
    output = tmp_path / "run"
    result = asyncio.run(provider.run_jev(manifest, output, limit=1, dollar_cap=5, request_cap=3))
    assert calls == 3 and result["completed"] == 1
    reservation = provider.reserve_tokens(manifest["jobs"][0]["request"]) * provider.INPUT_RATE
    assert result["cost_or_reserved_usd"] == pytest.approx(
        2 * reservation + 100 * provider.INPUT_RATE
    )
    state = read_jev_state(output, manifest)
    assert [attempt["status"] for attempt in state["attempts"]] == [
        "retryable_http",
        "retryable_http",
        "complete",
    ]
    previous = (output / "jev-ledger.jsonl").read_bytes()
    asyncio.run(provider.run_jev(manifest, output, dollar_cap=5, request_cap=3))
    assert calls == 3 and (output / "jev-ledger.jsonl").read_bytes() == previous


def test_concurrent_reservations_never_exceed_cap(
    manifest: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = routed_manifest(manifest, tmp_path, 2)
    original_client = httpx.AsyncClient
    calls = 0

    async def send(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.001)
        return httpx.Response(200, json=response(routed=True))

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(send)),
    )
    result = asyncio.run(
        provider.run_jev(manifest, tmp_path / "run", concurrency=4, dollar_cap=5, request_cap=2)
    )
    assert calls == result["completed"] == result["attempts"] == 2
    state = read_jev_state(tmp_path / "run", manifest)
    assert len(state["attempts"]) == len(state["results"]) == 2
    assert len(list((tmp_path / "run" / "jev-raw").glob("*.json"))) == 2
    metadata = json.loads((tmp_path / "run" / "jev-checkpoint.json").read_text())
    assert "results" not in metadata and "attempts" not in metadata


def test_interrupted_reservation_counts_on_resume_and_truncated_journal_fails(
    manifest: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = routed_manifest(manifest, tmp_path, 1)
    output = tmp_path / "run"
    initialize_ledger(output, manifest, 5, 1)
    job = manifest["jobs"][0]
    tokens = provider.reserve_tokens(job["request"])
    attempt = {
        "attempt_index": 0,
        "job_id": job["job_id"],
        "input_hash": job["input_hash"],
        "reserved_tokens": tokens,
        "reserved_cost": tokens * provider.INPUT_RATE,
        "number": 0,
        "status": "reserved",
    }
    append_event(output, {"event": "reserve", "attempt_index": 0, "attempt": attempt})
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    result = asyncio.run(provider.run_jev(manifest, output, dollar_cap=5, request_cap=1))
    assert result["attempts"] == 1 and result["completed"] == 0
    assert result["cost_or_reserved_usd"] == tokens * provider.INPUT_RATE
    with (output / "jev-ledger.jsonl").open("ab") as stream:
        stream.write(b'{"event":')
    with pytest.raises(ValueError, match="incomplete final event"):
        asyncio.run(provider.run_jev(manifest, output, dollar_cap=5, request_cap=1))


@pytest.mark.parametrize("quote", ["A  faster database.", "A\nfaster database."])
def test_quote_validator_normalizes_only_spacing(manifest: dict[str, Any], quote: str) -> None:
    batch = batches(manifest)[0]
    pairs = {pair["pair_id"]: pair for pair in manifest["pairs"]}
    raw = {
        "modelUsage": {JUDGE_MODEL: {}},
        "structured_output": {
            "labels": [
                {
                    "pair_id": key,
                    "label": "match",
                    "reason": "Fixture",
                    "evidence": quote,
                    "decisive_clause": "interest_1",
                }
                for key in batch["pair_ids"]
            ]
        },
    }
    assert len(validate_labels(raw, batch, pairs)) == len(batch["pair_ids"])
    assert judge.normalize_quote_typography("The “database’s” query") == 'The "database\'s" query'


@pytest.mark.parametrize(
    "quote",
    [
        "A faster ... database.",
        "A faster… database.",
        "A quicker database.",
        "A faster database!",
        " ",
    ],
)
def test_quote_validator_rejects_ellipsis_paraphrase_and_empty(
    manifest: dict[str, Any], quote: str
) -> None:
    batch = batches(manifest)[0]
    pairs = {pair["pair_id"]: pair for pair in manifest["pairs"]}
    raw = {
        "modelUsage": {JUDGE_MODEL: {}},
        "structured_output": {
            "labels": [
                {
                    "pair_id": key,
                    "label": "match",
                    "reason": "Fixture",
                    "evidence": quote,
                    "decisive_clause": "interest_1",
                }
                for key in batch["pair_ids"]
            ]
        },
    }
    with pytest.raises(ValueError):
        validate_labels(raw, batch, pairs)


def test_explicit_revalidation_promotes_full_stored_batch_without_calls(
    manifest: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "run"
    batch = batches(manifest)[0]
    raw = {
        "modelUsage": {JUDGE_MODEL: {}},
        "structured_output": {
            "labels": [
                {
                    "pair_id": key,
                    "label": "match",
                    "reason": "Fixture",
                    "evidence": "A  faster database.",
                    "decisive_clause": "interest_1",
                }
                for key in batch["pair_ids"]
            ]
        },
    }
    raw_path = output / "judge-raw" / (batch["batch_id"] + ".json")
    write_json(raw_path, raw)
    original = raw_path.read_bytes()
    state = {
        "manifest_hash": manifest["manifest_hash"],
        "max_calls": 3,
        "attempts": [
            {
                "batch_id": batch["batch_id"],
                "status": "invalid_or_incomplete",
                "halt": True,
                "audit": False,
            }
        ],
        "results": {},
        "audit_results": {},
    }
    write_json(output / "judge-checkpoint.json", state)

    def forbidden(_: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("Revalidation must never call the judge")

    monkeypatch.setattr(judge, "call_judge", forbidden)
    result = judge.run_judge(
        manifest, output, max_calls=3, revalidate_halted=[batch["batch_id"]], revalidate_only=True
    )
    assert result["calls"] == 1 and result["labels"] == len(batch["pair_ids"])
    assert not result["halted"] and result["promoted_batches"] == 1
    updated = json.loads((output / "judge-checkpoint.json").read_text())
    assert updated["attempts"] == state["attempts"]
    assert (
        updated["validation_promotions"][0]["validator_version"]
        == judge.REFERENCE_VALIDATOR_VERSION
    )
    assert raw_path.read_bytes() == original
    assert "recorded_at" in updated["validation_promotions"][0]
    accepted, provenance = accepted_references(manifest, output, updated)
    assert len(accepted) == len(batch["pair_ids"])
    assert provenance["batches"][0]["kind"] == "promoted"
    updated["validation_promotions"][0]["raw_hash"] = "wrong"
    with pytest.raises(ValueError, match="raw-response hash"):
        accepted_references(manifest, output, updated)


def test_revalidation_does_not_salvage_partially_invalid_batch(
    manifest: dict[str, Any], tmp_path: Path
) -> None:
    output = tmp_path / "run"
    batch = batches(manifest)[0]
    labels = [
        {
            "pair_id": key,
            "label": "match",
            "reason": "Fixture",
            "evidence": "A  faster database.",
            "decisive_clause": "interest_1",
        }
        for key in batch["pair_ids"]
    ]
    labels[-1]["evidence"] = "A faster ... database."
    raw = {"modelUsage": {JUDGE_MODEL: {}}, "structured_output": {"labels": labels}}
    write_json(output / "judge-raw" / (batch["batch_id"] + ".json"), raw)
    write_json(
        output / "judge-checkpoint.json",
        {
            "manifest_hash": manifest["manifest_hash"],
            "max_calls": 3,
            "attempts": [{"batch_id": batch["batch_id"], "halt": True}],
            "results": {},
            "audit_results": {},
        },
    )
    before = (output / "judge-checkpoint.json").read_bytes()
    with pytest.raises(ValueError, match="exact source quote"):
        judge.run_judge(
            manifest,
            output,
            max_calls=3,
            revalidate_halted=[batch["batch_id"]],
            revalidate_only=True,
        )
    assert before == (output / "judge-checkpoint.json").read_bytes()


def test_judge_subprocess_receives_sanitized_environment(
    manifest: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_run(command: list[str], **kwargs: Any) -> Any:
        captured.append(kwargs)
        return judge.subprocess.CompletedProcess(command, 0, stdout='{"result":"fixture"}')

    monkeypatch.setattr(judge.subprocess, "run", fake_run)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://invalid.example")
    monkeypatch.setenv("CLAUDECODE", "nested")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "test-secret")
    batch = batches(manifest)[0]
    assert judge.call_judge(batch) == {"result": "fixture"}
    assert len(captured) == 1
    child_environment = captured[0]["env"]
    assert "PATH" in child_environment
    assert not any(key.startswith(("ANTHROPIC_", "CLAUDE")) for key in child_environment)
    assert captured[0]["input"] == batch["prompt"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(ValueError, match="Refusing exported"):
        judge.call_judge(batch)
    assert len(captured) == 1


def test_accepted_reference_requires_attempt_and_unchanged_raw(
    manifest: dict[str, Any], tmp_path: Path
) -> None:
    output = tmp_path / "run"
    labels = [
        {
            "pair_id": pair["pair_id"],
            "label": "match",
            "reason": "Fixture",
            "evidence": "A faster database.",
            "decisive_clause": "interest_1",
        }
        for pair in manifest["pairs"]
    ]
    bind_labels(manifest, output, labels)
    state = json.loads((output / "judge-checkpoint.json").read_text())
    _, provenance = accepted_references(manifest, output, state)
    assert provenance["legacy_completed_without_recorded_hash"] == 0
    first_id = next(iter(state["results"]))
    state["results"][first_id]["labels"][0]["reason"] = "Changed after grading."
    with pytest.raises(ValueError, match="labels differ from their raw response"):
        accepted_references(manifest, output, state)
    state = json.loads((output / "judge-checkpoint.json").read_text())
    state["attempts"] = []
    with pytest.raises(ValueError, match="bind to one"):
        accepted_references(manifest, output, state)
    state = json.loads((output / "judge-checkpoint.json").read_text())
    state["attempts"][0]["raw_hash"] = "changed"
    with pytest.raises(ValueError, match="raw-response hash"):
        accepted_references(manifest, output, state)
    state = json.loads((output / "judge-checkpoint.json").read_text())
    del state["attempts"][0]["raw_hash"]
    _, provenance = accepted_references(manifest, output, state)
    assert provenance["legacy_completed_without_recorded_hash"] == 1


def test_exhausted_jobs_are_reported_without_expanding_retry_cap(
    manifest: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = routed_manifest(manifest, tmp_path, 3)
    original_client = httpx.AsyncClient
    calls = 0

    def send(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(529, headers={"Retry-After": "0"})

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(send)),
    )
    result = asyncio.run(
        provider.run_jev(manifest, tmp_path / "run", limit=1, dollar_cap=5, request_cap=3)
    )
    assert calls == 3 and result["completed"] == 0
    assert result["exhausted_jobs"] == [manifest["jobs"][0]["job_id"]]
    assert result["exhausted_job_count"] == 1
