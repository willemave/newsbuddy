"""Narrow, audited recovery of saved OpenRouter cent-rounding deviations."""

import fcntl
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import digest
from .transport import reported_cost, validate_transport


def failed_raw(
    output: Path, manifest: dict[str, Any], attempt: dict[str, Any], timestamp: str
) -> dict[str, Any]:
    validate_transport(manifest)
    if (
        attempt.get("status") != "invalid_response"
        or attempt.get("http_status") != 200
        or attempt.get("halt") is not True
        or "revalidation" in attempt
    ):
        raise ValueError("Recovery requires an original halted HTTP 200 invalid response")
    parsed_time = datetime.fromisoformat(timestamp)
    if parsed_time.utcoffset() != UTC.utcoffset(None):
        raise ValueError("Recovery timestamp must be UTC")
    raw_path = Path(attempt["raw_path"])
    expected_path = Path("jev-raw") / (
        f"{attempt['job_id']}.attempt-{attempt['attempt_index']}.json"
    )
    if raw_path != expected_path:
        raise ValueError("Recovery raw path does not identify this exact attempt")
    wrapper = json.loads((output / raw_path).read_text())
    if digest(wrapper) != attempt["raw_hash"] or wrapper["input_hash"] != attempt["input_hash"]:
        raise ValueError("Recovery raw-response hash/input mismatch")
    return dict(wrapper["raw_response"])


def recovery_validator_version(recorded: str | None) -> str:
    from .provider import RESPONSE_VALIDATOR_VERSION

    version = RESPONSE_VALIDATOR_VERSION if recorded is None else recorded
    if version not in {"choice-cent-rounding-v2"}:
        raise ValueError("Unsupported recovery response validator version")
    return version


def recovery_event(
    output: Path,
    manifest: dict[str, Any],
    attempt: dict[str, Any],
    timestamp: str,
    *,
    recorded_validator_version: str | None = None,
) -> dict[str, Any]:
    from .provider import validate_response

    version = recovery_validator_version(recorded_validator_version)

    raw = failed_raw(output, manifest, attempt, timestamp)
    normalized = validate_response(raw, manifest["model"])
    probabilities = list(normalized["probabilities"].values())
    if not (
        any(math.isclose(sum(probabilities), total, abs_tol=1e-9) for total in (0.99, 1.01))
        and all(
            math.isclose(value * 100, round(value * 100), abs_tol=1e-9) for value in probabilities
        )
    ):
        raise ValueError("Recovery only permits the observed cent-rounding sum deviation")
    actual = reported_cost(normalized["usage"])
    if (
        actual > attempt["reserved_cost"]
        or normalized["usage"]["input_tokens"] > attempt["reserved_tokens"]
    ):
        raise ValueError("Recovery usage exceeds original reservation")
    result = {
        **normalized,
        "input_hash": attempt["input_hash"],
        "latency_seconds": attempt["latency_seconds"],
    }
    return {
        "event": "revalidate",
        "attempt_index": attempt["attempt_index"],
        "previous_raw_hash": attempt["raw_hash"],
        "validator_version": version,
        "timestamp": timestamp,
        "outcome": {
            "status": "complete",
            "halt": False,
            "http_status": 200,
            "actual_cost": actual,
            "latency_seconds": attempt["latency_seconds"],
            "raw_path": attempt["raw_path"],
            "raw_hash": attempt["raw_hash"],
        },
        "result": result,
    }


def require_unused_generation(
    output: Path, attempts: list[dict[str, Any]], attempt_index: int, generation_id: str
) -> None:
    """Reject reused identities before appending an irreversible recovery event."""
    for other in attempts:
        if other["attempt_index"] == attempt_index or not other.get("raw_path"):
            continue
        path = Path(other["raw_path"])
        if path.is_absolute() or path.parent != Path("jev-raw"):
            raise ValueError("Invalid raw-response path")
        wrapper = json.loads((output / path).read_text())
        if digest(wrapper) != other["raw_hash"] or wrapper["input_hash"] != other["input_hash"]:
            raise ValueError("Prior attempt raw hash/input mismatch")
        raw = wrapper["raw_response"]
        if isinstance(raw, dict) and raw.get("id") == generation_id:
            raise ValueError("Duplicate known generation ID")


def revalidate_jev(manifest: dict[str, Any], output: Path, attempt_index: int) -> dict[str, Any]:
    from .ledger import append_event, read_jev_state

    with (output / "jev.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = read_jev_state(output, manifest)
        if not 0 <= attempt_index < len(state["attempts"]):
            raise ValueError("Unknown Jev attempt index")
        attempt = state["attempts"][attempt_index]
        event = recovery_event(output, manifest, attempt, datetime.now(UTC).isoformat())
        if attempt["job_id"] in state["results"] or any(
            result.get("generation_id") == event["result"]["generation_id"]
            for result in state["results"].values()
        ):
            raise ValueError("Recovery would duplicate an accepted job or generation ID")
        require_unused_generation(
            output, state["attempts"], attempt_index, event["result"]["generation_id"]
        )
        append_event(output, event)
        recovered = read_jev_state(output, manifest)
        return {
            "revalidated_attempt": attempt_index,
            "validator_version": event["validator_version"],
            "attempts": len(recovered["attempts"]),
            "completed": len(recovered["results"]),
            "halted": any(item.get("halt") for item in recovered["attempts"]),
        }


def rejected_choice(
    output: Path, manifest: dict[str, Any], attempt: dict[str, Any]
) -> dict[str, Any]:
    """Verify a rejected response's full envelope, with precisely the typed choice failure."""
    from .provider import ChoiceMismatch, validate_response

    validate_transport(manifest)
    expected = Path("jev-raw") / f"{attempt['job_id']}.attempt-{attempt['attempt_index']}.json"
    if attempt.get("raw_path") != str(expected):
        raise ValueError("Rejected-choice raw path mismatch")
    wrapper = json.loads((output / expected).read_text())
    if digest(wrapper) != attempt["raw_hash"] or wrapper["input_hash"] != attempt["input_hash"]:
        raise ValueError("Rejected-choice raw hash/input mismatch")
    try:
        validate_response(wrapper["raw_response"], manifest["model"])
    except ChoiceMismatch as error:
        normalized = error.normalized
    else:
        raise ValueError("Acknowledgment requires a non-argmax choice violation")
    if (
        reported_cost(normalized["usage"]) > attempt["reserved_cost"]
        or normalized["usage"]["input_tokens"] > attempt["reserved_tokens"]
    ):
        raise ValueError("Rejected-choice usage exceeds original reservation")
    return dict(normalized)


def acknowledge_event(
    output: Path,
    manifest: dict[str, Any],
    attempt: dict[str, Any],
    timestamp: str,
    *,
    recorded_validator_version: str | None = None,
) -> dict[str, Any]:
    version = recovery_validator_version(recorded_validator_version)

    failed_raw(output, manifest, attempt, timestamp)
    normalized = rejected_choice(output, manifest, attempt)
    return {
        "event": "acknowledge",
        "attempt_index": attempt["attempt_index"],
        "previous_raw_hash": attempt["raw_hash"],
        "validator_version": version,
        "timestamp": timestamp,
        "violation_code": "choice_probability_mismatch",
        "generation_id": normalized["generation_id"],
        "outcome": {
            "status": "invalid_response",
            "halt": False,
            "http_status": 200,
            "error_code": "choice_probability_mismatch",
            "actual_cost": reported_cost(normalized["usage"]),
            "latency_seconds": attempt["latency_seconds"],
            "raw_path": attempt["raw_path"],
            "raw_hash": attempt["raw_hash"],
        },
        "result": None,
    }


def acknowledge_jev(manifest: dict[str, Any], output: Path, attempt_index: int) -> dict[str, Any]:
    from .ledger import append_event, read_jev_state

    with (output / "jev.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = read_jev_state(output, manifest)
        if not 0 <= attempt_index < len(state["attempts"]):
            raise ValueError("Unknown Jev attempt index")
        attempt = state["attempts"][attempt_index]
        event = acknowledge_event(output, manifest, attempt, datetime.now(UTC).isoformat())
        require_unused_generation(output, state["attempts"], attempt_index, event["generation_id"])
        append_event(output, event)
        final = read_jev_state(output, manifest)
        return {
            "acknowledged_attempt": attempt_index,
            "attempts": len(final["attempts"]),
            "completed": len(final["results"]),
            "halted": any(item.get("halt") for item in final["attempts"]),
            "violation_code": event["violation_code"],
        }
