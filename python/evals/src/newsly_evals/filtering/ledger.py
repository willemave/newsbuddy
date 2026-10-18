"""Append-only Jev usage journal, with raw responses outside the journal."""

import json
import math
import os
from pathlib import Path
from typing import Any

from .artifacts import digest, write_json
from .transport import OPENROUTER_ROUTE, reported_cost, validate_transport


def append_event(output: Path, event: dict[str, Any]) -> None:
    """Fsync every reservation before network dispatch; never rewrite prior events."""
    encoded = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode()
    with (output / "jev-ledger.jsonl").open("ab") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def initialize_ledger(
    output: Path, manifest: dict[str, Any], dollar_cap: float, request_cap: int
) -> dict[str, Any]:
    from .provider import RESPONSE_VALIDATOR_VERSION

    path = output / "jev-checkpoint.json"
    metadata = {
        "schema_version": 2,
        "manifest_hash": manifest["manifest_hash"],
        "dollar_cap": dollar_cap,
        "request_cap": request_cap,
        "input_rate_usd": 0.042 / 1_000_000,
    }
    if manifest["schema_version"] == 2:
        validate_transport(manifest)
        metadata.update({"schema_version": 3, "transport": OPENROUTER_ROUTE})
    if path.exists():
        previous = json.loads(path.read_text())
        previous.pop("response_validator_version", None)
        if previous != metadata:
            raise ValueError("Resume must preserve original caps and frozen manifest")
    else:
        if (output / "jev-ledger.jsonl").exists():
            raise ValueError("Orphaned Jev ledger requires inspection")
        write_json(path, {**metadata, "response_validator_version": RESPONSE_VALIDATOR_VERSION})
    return read_jev_state(output, manifest)


def read_jev_state(output: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    metadata = json.loads((output / "jev-checkpoint.json").read_text())
    expected_version = 3 if manifest["schema_version"] == 2 else 2
    if (
        metadata.get("schema_version") != expected_version
        or metadata["manifest_hash"] != manifest["manifest_hash"]
    ):
        raise ValueError("Jev ledger manifest/version mismatch")
    if expected_version == 3:
        validate_transport(manifest)
        if metadata.get("transport") != OPENROUTER_ROUTE:
            raise ValueError("Ledger transport differs from pinned route")
    jobs = {job["job_id"]: job for job in manifest["jobs"]}
    state: dict[str, Any] = {
        **metadata,
        "response_validator_version": metadata.get(
            "response_validator_version", "legacy-strict-sum-unversioned"
        ),
        "attempts": [],
        "results": {},
    }
    path = output / "jev-ledger.jsonl"
    if not path.exists():
        return state
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise ValueError("Jev journal has an incomplete final event; inspect before resuming")
    finished = set()
    generation_ids: dict[str, int] = {}
    for line in data.splitlines():
        event = json.loads(line)
        index = event["attempt_index"]
        if event["event"] == "reserve":
            attempt = event["attempt"]
            if index != len(state["attempts"]) or attempt["attempt_index"] != index:
                raise ValueError("Nonsequential Jev reservation")
            job = jobs.get(attempt["job_id"])
            if job is None or attempt["input_hash"] != job["input_hash"]:
                raise ValueError("Jev reservation input identity mismatch")
            cost = attempt["reserved_cost"]
            if not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
                raise ValueError("Invalid Jev reservation cost")
            if cost != attempt["reserved_tokens"] * metadata["input_rate_usd"]:
                raise ValueError("Jev reservation rate mismatch")
            state["attempts"].append(attempt)
        elif event["event"] in ("finish", "revalidate", "acknowledge"):
            recovery = event["event"] != "finish"
            if not 0 <= index < len(state["attempts"]) or (
                index not in finished if recovery else index in finished
            ):
                raise ValueError("Unreserved or duplicate Jev completion")
            finished.add(index)
            attempt = state["attempts"][index]
            if recovery:
                from .recovery import acknowledge_event, recovery_event

                builder = acknowledge_event if event["event"] == "acknowledge" else recovery_event
                expected = builder(
                    output,
                    manifest,
                    attempt,
                    event["timestamp"],
                    recorded_validator_version=event["validator_version"],
                )
                if event != expected:
                    raise ValueError("Revalidation event differs from exact saved response")
                audit_key = (
                    "protocol_acknowledgement"
                    if event["event"] == "acknowledge"
                    else "revalidation"
                )
                attempt[audit_key] = {
                    "original_rejection": dict(attempt),
                    "validator_version": event["validator_version"],
                    "previous_raw_hash": event["previous_raw_hash"],
                    "timestamp": event["timestamp"],
                }
            outcome = event["outcome"]
            if any(
                key
                not in {
                    "status",
                    "actual_cost",
                    "http_status",
                    "latency_seconds",
                    "halt",
                    "error_code",
                    "raw_path",
                    "raw_hash",
                }
                for key in outcome
            ):
                raise ValueError("Invalid Jev outcome fields")
            actual = outcome.get("actual_cost")
            if actual is not None and (
                not isinstance(actual, (int, float)) or not math.isfinite(actual) or actual < 0
            ):
                raise ValueError("Invalid metered Jev cost")
            if outcome.get("raw_path"):
                observed_path = Path(outcome["raw_path"])
                if observed_path.is_absolute() or observed_path.parent != Path("jev-raw"):
                    raise ValueError("Invalid raw-response path")
                observed = json.loads((output / observed_path).read_text())
                if (
                    digest(observed) != outcome["raw_hash"]
                    or observed["input_hash"] != attempt["input_hash"]
                ):
                    raise ValueError("Raw-response hash/input mismatch")
                raw_body = observed["raw_response"]
                observed_id = raw_body.get("id") if isinstance(raw_body, dict) else None
                if isinstance(observed_id, str) and observed_id.strip():
                    generation_ids.setdefault(observed_id, index)
            attempt.update(outcome)
            result = event.get("result")
            if outcome["status"] == "invalid_choice" or event["event"] == "acknowledge":
                from .recovery import rejected_choice

                rejected = rejected_choice(output, manifest, attempt)
                if (
                    result is not None
                    or outcome.get("http_status") != 200
                    or outcome.get("error_code") != "choice_probability_mismatch"
                    or actual != reported_cost(rejected["usage"])
                ):
                    raise ValueError("Invalid rejected-choice outcome")
                if generation_ids.get(rejected["generation_id"], index) != index:
                    raise ValueError("Duplicate rejected-choice generation ID")
                generation_ids[rejected["generation_id"]] = index
            if result is not None:
                job_id = attempt["job_id"]
                if job_id in state["results"] or result["input_hash"] != attempt["input_hash"]:
                    raise ValueError("Duplicate or mismatched Jev result")
                raw_path = Path(outcome["raw_path"])
                if raw_path.is_absolute() or raw_path.parent != Path("jev-raw"):
                    raise ValueError("Invalid raw-response path")
                wrapper = json.loads((output / raw_path).read_text())
                if (
                    digest(wrapper) != outcome["raw_hash"]
                    or wrapper["input_hash"] != result["input_hash"]
                ):
                    raise ValueError("Raw-response hash/input mismatch")
                metered = (
                    reported_cost(result["usage"])
                    if expected_version == 3
                    else result["usage"]["input_tokens"] * metadata["input_rate_usd"]
                )
                if actual != metered:
                    raise ValueError("Result usage differs from metered cost")
                if expected_version == 3:
                    generation_id = result.get("generation_id")
                    if not isinstance(generation_id, str) or not generation_id.strip():
                        raise ValueError("Missing OpenRouter generation ID")
                    if generation_ids.get(generation_id, index) != index:
                        raise ValueError("Duplicate OpenRouter generation ID")
                    generation_ids[generation_id] = index
                    if actual > attempt["reserved_cost"]:
                        raise ValueError("Metered cost exceeds reservation")
                if outcome["status"] != "complete":
                    raise ValueError("Nonterminal Jev result")
                state["results"][job_id] = {**result, "raw_response": wrapper["raw_response"]}
        else:
            raise ValueError("Unknown Jev ledger event")
    if len(state["attempts"]) > metadata["request_cap"]:
        raise ValueError("Jev ledger exceeds persisted request cap")
    return state


def persist_raw(output: Path, attempt: dict[str, Any], raw: Any) -> dict[str, str]:
    relative = Path("jev-raw") / f"{attempt['job_id']}.attempt-{attempt['attempt_index']}.json"
    wrapper = {"input_hash": attempt["input_hash"], "raw_response": raw}
    write_json(output / relative, wrapper)
    return {"raw_path": str(relative), "raw_hash": digest(wrapper)}
