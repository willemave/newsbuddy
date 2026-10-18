"""Offline recovery keeps a rejected invocation and its raw evidence immutable."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from test_filter_openrouter import fixture_run, raw_response

from newsly_evals.filtering.artifacts import write_json
from newsly_evals.filtering.ledger import (
    append_event,
    initialize_ledger,
    persist_raw,
    read_jev_state,
)
from newsly_evals.filtering.recovery import recovery_event, revalidate_jev


def failed_run(output: Path, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = fixture_run(output)
    initialize_ledger(output, manifest, 0.01, 16)
    job = manifest["jobs"][0]
    attempt = {
        "attempt_index": 0,
        "job_id": job["job_id"],
        "input_hash": job["input_hash"],
        "reserved_tokens": 4096,
        "reserved_cost": 4096 * 0.042 / 1_000_000,
        "status": "reserved",
    }
    append_event(output, {"event": "reserve", "attempt_index": 0, "attempt": attempt})
    if raw is None:
        raw = raw_response()
        raw["answers"]["filter"]["probabilities"]["match"] = 0.89
    outcome = {
        "status": "invalid_response",
        "halt": True,
        "http_status": 200,
        "latency_seconds": 1.234,
        **persist_raw(output, attempt, raw),
    }
    append_event(
        output, {"event": "finish", "attempt_index": 0, "outcome": outcome, "result": None}
    )
    return manifest


def test_recovery_is_additive_preserves_usage_raw_and_failed_history(tmp_path: Path) -> None:
    manifest = failed_run(tmp_path)
    ledger = tmp_path / "jev-ledger.jsonl"
    original = ledger.read_bytes()
    raw_path = next((tmp_path / "jev-raw").glob("*.json"))
    original_raw = raw_path.read_bytes()
    result = revalidate_jev(manifest, tmp_path, 0)
    assert result["attempts"] == 1 and result["completed"] == 1 and not result["halted"]
    assert ledger.read_bytes().startswith(original)
    assert raw_path.read_bytes() == original_raw
    state = read_jev_state(tmp_path, manifest)
    attempt = state["attempts"][0]
    assert attempt["revalidation"]["original_rejection"]["halt"] is True
    assert attempt["actual_cost"] == 0.0000042
    assert state["results"]["0"]["probabilities"]["match"] == 0.89
    assert state["results"]["0"]["latency_seconds"] == 1.234
    with pytest.raises(ValueError, match="original halted"):
        revalidate_jev(manifest, tmp_path, 0)


@pytest.mark.parametrize("bad", ["valid_sum", "provider", "model", "choice", "cost", "off_grid"])
def test_recovery_is_not_a_generic_halt_clear(tmp_path: Path, bad: str) -> None:
    raw = raw_response()
    raw["answers"]["filter"]["probabilities"]["match"] = 0.89
    if bad == "valid_sum":
        raw["answers"]["filter"]["probabilities"]["match"] = 0.9
    elif bad in ("provider", "model"):
        raw[bad] = "wrong"
    elif bad == "choice":
        raw["answers"]["filter"]["choice"] = "no_match"
    elif bad == "cost":
        raw["usage"]["cost"] = 0.99
    else:
        raw["answers"]["filter"]["probabilities"].update(match=0.891, no_match=0.079)
    manifest = failed_run(tmp_path, raw)
    original = (tmp_path / "jev-ledger.jsonl").read_bytes()
    with pytest.raises(ValueError):
        revalidate_jev(manifest, tmp_path, 0)
    assert (tmp_path / "jev-ledger.jsonl").read_bytes() == original


@pytest.mark.parametrize("field", ["validator_version", "previous_raw_hash", "result", "timestamp"])
def test_replay_rejects_tampered_recovery(tmp_path: Path, field: str) -> None:
    manifest = failed_run(tmp_path)
    state = read_jev_state(tmp_path, manifest)
    event = recovery_event(tmp_path, manifest, state["attempts"][0], "2026-10-17T20:00:00+00:00")
    if field == "result":
        event[field]["probabilities"]["match"] = 0.9
    elif field == "timestamp":
        event[field] = "2026-10-17T20:00:00-07:00"
    else:
        event[field] = "tampered"
    append_event(tmp_path, event)
    with pytest.raises(ValueError):
        read_jev_state(tmp_path, manifest)


def test_tampered_raw_cannot_be_promoted(tmp_path: Path) -> None:
    manifest = failed_run(tmp_path)
    path = next((tmp_path / "jev-raw").glob("*.json"))
    wrapper = json.loads(path.read_text())
    wrapper["raw_response"]["id"] = "changed"
    write_json(path, wrapper)
    with pytest.raises(ValueError, match="hash/input"):
        revalidate_jev(manifest, tmp_path, 0)


def test_duplicate_generation_rejected_before_append(tmp_path: Path) -> None:
    manifest = failed_run(tmp_path)
    state = read_jev_state(tmp_path, manifest)
    attempt = copy.deepcopy(state["attempts"][0])
    second_job = manifest["jobs"][1]
    attempt.update(
        attempt_index=1, job_id=second_job["job_id"], input_hash=second_job["input_hash"]
    )
    append_event(tmp_path, {"event": "reserve", "attempt_index": 1, "attempt": attempt})
    raw = raw_response()
    from newsly_evals.filtering.provider import validate_response

    result = {**validate_response(raw, manifest["model"]), "input_hash": attempt["input_hash"]}
    outcome = {
        "status": "complete",
        "actual_cost": 0.0000042,
        **persist_raw(tmp_path, attempt, raw),
    }
    append_event(
        tmp_path, {"event": "finish", "attempt_index": 1, "outcome": outcome, "result": result}
    )
    before = (tmp_path / "jev-ledger.jsonl").read_bytes()
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        revalidate_jev(manifest, tmp_path, 0)
    assert (tmp_path / "jev-ledger.jsonl").read_bytes() == before


def test_recovery_holds_the_provider_run_lock(tmp_path: Path) -> None:
    import fcntl

    manifest = failed_run(tmp_path)
    with (tmp_path / "jev.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            revalidate_jev(manifest, tmp_path, 0)


def test_legacy_checkpoint_is_preserved_on_resume(tmp_path: Path) -> None:
    manifest = failed_run(tmp_path)
    path = tmp_path / "jev-checkpoint.json"
    metadata = json.loads(path.read_text())
    metadata.pop("response_validator_version")
    write_json(path, metadata)
    before = path.read_bytes()
    state = initialize_ledger(tmp_path, manifest, 0.01, 16)
    assert path.read_bytes() == before
    assert state["response_validator_version"] == "legacy-strict-sum-unversioned"


@pytest.mark.parametrize("change", ["status", "tokens"])
def test_original_failure_and_usage_limits_are_required(tmp_path: Path, change: str) -> None:
    raw = raw_response()
    raw["answers"]["filter"]["probabilities"]["match"] = 0.89
    if change == "tokens":
        raw["usage"].update(input_tokens=5000, cost=5000 * 0.042 / 1_000_000)
    manifest = failed_run(tmp_path, raw)
    attempt = read_jev_state(tmp_path, manifest)["attempts"][0]
    if change == "status":
        attempt["http_status"] = 429
    with pytest.raises(ValueError):
        recovery_event(tmp_path, manifest, attempt, "2026-10-17T20:00:00+00:00")


def mismatched_raw() -> dict[str, Any]:
    raw = raw_response()
    raw["answers"]["filter"].update(
        choice="no_match",
        probabilities={"match": 0.49, "no_match": 0.48, "insufficient_evidence": 0.03},
    )
    return raw


def test_acknowledgment_keeps_failure_and_cost_without_accepting_result(tmp_path: Path) -> None:
    from newsly_evals.filtering.recovery import acknowledge_jev

    manifest = failed_run(tmp_path, mismatched_raw())
    before = (tmp_path / "jev-ledger.jsonl").read_bytes()
    result = acknowledge_jev(manifest, tmp_path, 0)
    assert result["attempts"] == 1 and result["completed"] == 0 and not result["halted"]
    assert (tmp_path / "jev-ledger.jsonl").read_bytes().startswith(before)
    state = read_jev_state(tmp_path, manifest)
    attempt = state["attempts"][0]
    assert attempt["status"] == "invalid_response"
    assert attempt["actual_cost"] == 0.0000042
    assert attempt["protocol_acknowledgement"]["original_rejection"]["halt"]
    assert not state["results"]
    with pytest.raises(ValueError):
        acknowledge_jev(manifest, tmp_path, 0)


@pytest.mark.parametrize("bad", ["valid_choice", "model", "provider", "cost", "sum"])
def test_acknowledgment_only_allows_metered_choice_violation(tmp_path: Path, bad: str) -> None:
    from newsly_evals.filtering.recovery import acknowledge_jev

    raw = mismatched_raw()
    if bad == "valid_choice":
        raw["answers"]["filter"]["choice"] = "match"
    elif bad in ("model", "provider"):
        raw[bad] = "wrong"
    elif bad == "cost":
        raw["usage"]["cost"] = 0.99
    else:
        raw["answers"]["filter"]["probabilities"]["match"] = 0.8
    manifest = failed_run(tmp_path, raw)
    before = (tmp_path / "jev-ledger.jsonl").read_bytes()
    with pytest.raises(ValueError):
        acknowledge_jev(manifest, tmp_path, 0)
    assert (tmp_path / "jev-ledger.jsonl").read_bytes() == before


def test_acknowledgment_replay_rejects_changed_raw_binding(tmp_path: Path) -> None:
    from newsly_evals.filtering.recovery import acknowledge_event

    manifest = failed_run(tmp_path, mismatched_raw())
    state = read_jev_state(tmp_path, manifest)
    event = acknowledge_event(tmp_path, manifest, state["attempts"][0], "2026-10-17T20:00:00+00:00")
    event["previous_raw_hash"] = "changed"
    append_event(tmp_path, event)
    with pytest.raises(ValueError):
        read_jev_state(tmp_path, manifest)


def test_acknowledgment_refuses_id_seen_in_another_rejected_attempt(tmp_path: Path) -> None:
    from newsly_evals.filtering.recovery import acknowledge_jev

    manifest = failed_run(tmp_path, mismatched_raw())
    attempt = copy.deepcopy(read_jev_state(tmp_path, manifest)["attempts"][0])
    second = manifest["jobs"][1]
    attempt.update(attempt_index=1, job_id=second["job_id"], input_hash=second["input_hash"])
    append_event(tmp_path, {"event": "reserve", "attempt_index": 1, "attempt": attempt})
    outcome = {
        "status": "invalid_response",
        "halt": True,
        "http_status": 200,
        "latency_seconds": 2.0,
        **persist_raw(tmp_path, attempt, mismatched_raw()),
    }
    append_event(
        tmp_path, {"event": "finish", "attempt_index": 1, "outcome": outcome, "result": None}
    )
    before = (tmp_path / "jev-ledger.jsonl").read_bytes()
    with pytest.raises(ValueError, match="known generation"):
        acknowledge_jev(manifest, tmp_path, 0)
    assert (tmp_path / "jev-ledger.jsonl").read_bytes() == before


def test_automatic_invalid_choice_ledger_contract(tmp_path: Path) -> None:
    manifest = failed_run(tmp_path, mismatched_raw())
    path = tmp_path / "jev-ledger.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events[-1]["outcome"].update(
        status="invalid_choice",
        halt=False,
        actual_cost=0.0000042,
        error_code="choice_probability_mismatch",
    )
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    state = read_jev_state(tmp_path, manifest)
    assert not state["results"] and not state["attempts"][0]["halt"]
    events[-1]["outcome"]["actual_cost"] = 0
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    with pytest.raises(ValueError, match="Invalid rejected-choice"):
        read_jev_state(tmp_path, manifest)


@pytest.mark.parametrize("kind", ["revalidate", "acknowledge"])
def test_old_events_replay_using_recorded_validator_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from newsly_evals.filtering import provider
    from newsly_evals.filtering.recovery import acknowledge_jev

    manifest = failed_run(tmp_path, mismatched_raw() if kind == "acknowledge" else None)
    if kind == "acknowledge":
        acknowledge_jev(manifest, tmp_path, 0)
    else:
        revalidate_jev(manifest, tmp_path, 0)
    before = (tmp_path / "jev-ledger.jsonl").read_bytes()
    monkeypatch.setattr(provider, "RESPONSE_VALIDATOR_VERSION", "future-validator-version")
    state = read_jev_state(tmp_path, manifest)
    assert not state["attempts"][0]["halt"]
    assert (tmp_path / "jev-ledger.jsonl").read_bytes() == before


def test_unknown_recorded_validator_version_is_rejected(tmp_path: Path) -> None:
    from newsly_evals.filtering.recovery import acknowledge_event

    manifest = failed_run(tmp_path, mismatched_raw())
    attempt = read_jev_state(tmp_path, manifest)["attempts"][0]
    with pytest.raises(ValueError, match="Unsupported recovery"):
        acknowledge_event(
            tmp_path,
            manifest,
            attempt,
            "2026-10-17T20:00:00+00:00",
            recorded_validator_version="unknown-version",
        )


@pytest.mark.parametrize(
    "recover_index,raw_kind", [(0, "duplicate"), (1, "duplicate"), (0, "array"), (0, "null")]
)
def test_recovery_checks_other_rejected_attempts_before_append(
    tmp_path: Path, recover_index: int, raw_kind: str
) -> None:
    manifest = failed_run(tmp_path)
    first = read_jev_state(tmp_path, manifest)["attempts"][0]
    job = manifest["jobs"][1]
    attempt = {
        "attempt_index": 1,
        "job_id": job["job_id"],
        "input_hash": job["input_hash"],
        "reserved_tokens": first["reserved_tokens"],
        "reserved_cost": first["reserved_cost"],
        "status": "reserved",
    }
    append_event(tmp_path, {"event": "reserve", "attempt_index": 1, "attempt": attempt})
    raw = raw_response()
    raw["answers"]["filter"]["probabilities"]["match"] = 0.89
    append_event(
        tmp_path,
        {
            "event": "finish",
            "attempt_index": 1,
            "result": None,
            "outcome": {
                "status": "invalid_response",
                "halt": True,
                "http_status": 200,
                "latency_seconds": 1.234,
                **persist_raw(
                    tmp_path,
                    attempt,
                    raw if raw_kind == "duplicate" else ([] if raw_kind == "array" else None),
                ),
            },
        },
    )
    ledger = tmp_path / "jev-ledger.jsonl"
    before = ledger.read_bytes()
    if raw_kind == "duplicate":
        with pytest.raises(ValueError, match="Duplicate"):
            revalidate_jev(manifest, tmp_path, recover_index)
        assert ledger.read_bytes() == before
    else:
        assert revalidate_jev(manifest, tmp_path, recover_index)["completed"] == 1
    assert len(read_jev_state(tmp_path, manifest)["attempts"]) == 2
