#!/usr/bin/env python3
"""Run a frozen category manifest with bounded, durable, never-retried Jev attempts."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
import os
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import httpx

from newsly_evals.filtering.transport import (
    OPENROUTER_PROVIDER,
    OPENROUTER_ROUTE,
    reported_cost,
)


def encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def durable_write(path: Path, data: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def append_event(path: Path, event: dict[str, Any]) -> None:
    with path.open("ab") as stream:
        stream.write(encode(event) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def metered_cost(raw: dict[str, Any]) -> float:
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("missing_usage")
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("invalid_usage")
    return reported_cost(usage)


def validate_response(raw: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    if raw.get("error") is not None:
        raise ValueError("provider_error")
    if raw.get("model") != OPENROUTER_ROUTE["response_model"]:
        raise ValueError("wrong_model")
    if raw.get("provider") != OPENROUTER_ROUTE["provider"]:
        raise ValueError("wrong_provider")
    if not isinstance(raw.get("id"), str) or not raw["id"].strip():
        raise ValueError("missing_generation_id")
    cost = metered_cost(raw)
    answers = raw.get("answers")
    answer = answers.get("category") if isinstance(answers, dict) else None
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise ValueError("invalid_category_answer")
    probabilities = answer.get("probabilities")
    criteria = request["questions"]["category"]["criteria"]
    if not isinstance(probabilities, dict) or set(probabilities) != set(criteria):
        raise ValueError("invalid_probability_keys")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 1
        for value in probabilities.values()
    ):
        raise ValueError("invalid_probability")
    cent_rounded = all(abs(value - round(value, 2)) < 1e-12 for value in probabilities.values())
    tolerance = len(probabilities) * 0.005 + 1e-12 if cent_rounded else 1e-8
    if abs(sum(probabilities.values()) - 1) > tolerance:
        raise ValueError("probabilities_not_normalized")
    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in probabilities:
        raise ValueError("invalid_choice")
    if probabilities[choice] < max(probabilities.values()) - 1e-8:
        raise ValueError("choice_not_argmax")
    return {
        "generation_id": raw["id"],
        "model": raw["model"],
        "provider": raw["provider"],
        "choice": choice,
        "probabilities": probabilities,
        "cost_usd": cost,
    }


def validate_manifest(manifest: dict[str, Any]) -> None:
    cap, dollars = manifest.get("request_cap"), manifest.get("dollar_cap")
    if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= 300:
        raise ValueError("request_cap must be 1..300")
    if (
        isinstance(dollars, bool)
        or not isinstance(dollars, (int, float))
        or not math.isfinite(dollars)
        or not 0 < dollars <= 0.1
    ):
        raise ValueError("dollar_cap must be positive and at most 0.1")
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("jobs must be a nonempty list")
    seen = set()
    for job in jobs:
        identifier, request = job.get("job_id"), job.get("request")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("job_id must be a unique nonempty string")
        seen.add(identifier)
        if not isinstance(request, dict) or (
            request.get("model") != OPENROUTER_ROUTE["request_model"]
            or request.get("provider") != OPENROUTER_PROVIDER
        ):
            raise ValueError("request must use the pinned OpenRouter route")
        question = request.get("questions", {}).get("category", {})
        criteria = question.get("criteria")
        if question.get("type") != "choice" or not isinstance(criteria, dict) or not criteria:
            raise ValueError("category must be a choice question with nonempty criteria")
        encode(request)


async def run(
    manifest_path: Path,
    output: Path,
    limit: int = 4,
    concurrency: int = 1,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Limit is cumulative attempts, including failures and unresolved reservations."""
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    validate_manifest(manifest)
    if not 1 <= concurrency <= 4 or not 1 <= limit <= manifest["request_cap"]:
        raise ValueError("concurrency must be 1..4 and limit within request_cap")
    output.mkdir(parents=True, exist_ok=True)
    with (output / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return await run_locked(manifest, manifest_bytes, output, limit, concurrency, client)


async def run_locked(
    manifest: dict[str, Any],
    manifest_bytes: bytes,
    output: Path,
    limit: int,
    concurrency: int,
    client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    binding = {
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "transport": OPENROUTER_ROUTE,
        "reservation_overhead_tokens": 3000,
        "version": 1,
    }
    binding_path = output / "run.json"
    if binding_path.exists():
        if json.loads(binding_path.read_bytes()) != binding:
            raise ValueError("Manifest or runner contract changed on resume")
    else:
        if (output / "attempts.jsonl").exists():
            raise ValueError("Ledger exists without manifest binding")
        durable_write(binding_path, encode(binding) + b"\n")
    ledger = output / "attempts.jsonl"
    if not ledger.exists():
        durable_write(ledger, b"")
    events = [json.loads(line) for line in ledger.read_text().splitlines() if line]
    reservations = {event["job_id"]: event for event in events if event["event"] == "reserve"}
    outcomes = {event["job_id"]: event for event in events if event["event"] == "result"}
    if len(reservations) != sum(event["event"] == "reserve" for event in events):
        raise ValueError("Duplicate reservation in ledger")
    if set(outcomes) - set(reservations):
        raise ValueError("Unreserved ledger result")
    generation_ids = {
        event["generation_id"] for event in outcomes.values() if event.get("generation_id")
    }
    spent = sum(
        event["reserved_usd"]
        if outcomes.get(key, {}).get("cost_usd") is None
        else outcomes[key]["cost_usd"]
        for key, event in reservations.items()
    )
    halted = any(event.get("halt") for event in outcomes.values())
    raw_dir = output / "raw"
    raw_dir.mkdir(exist_ok=True)
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY is required")
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=120, follow_redirects=False)
    assert client is not None

    async def attempt(job: dict[str, Any], reservation: float, index: int) -> None:
        nonlocal spent, halted
        started = time.monotonic()
        result: dict[str, Any] = {
            "event": "result",
            "job_id": job["job_id"],
            "status": "failed",
            "cost_usd": None,
            "raw_file": f"raw/{index:04d}.json",
        }
        try:
            response = await client.post(
                str(OPENROUTER_ROUTE["endpoint"]),
                content=encode(job["request"]),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            # The original body survives invalid JSON, failed HTTP, and schema failures.
            durable_write(raw_dir / f"{index:04d}.json", response.content)
            raw = response.json()
            if not isinstance(raw, dict):
                raise ValueError("invalid_envelope")
            try:
                result["cost_usd"] = metered_cost(raw)
            except (ValueError, KeyError, TypeError):
                result["halt"] = True
                usage = raw.get("usage")
                billed = usage.get("cost") if isinstance(usage, dict) else None
                if (
                    not isinstance(billed, bool)
                    and isinstance(billed, (int, float))
                    and math.isfinite(billed)
                    and billed >= 0
                ):
                    # Retain a reported charge even when the frozen price contract fails.
                    result["cost_usd"] = max(float(billed), reservation)
            if result["cost_usd"] is not None and result["cost_usd"] > reservation:
                result["halt"] = True
                raise ValueError("cost_exceeds_reservation")
            identifier = raw.get("id")
            if isinstance(identifier, str) and identifier.strip():
                result["generation_id"] = identifier
                if identifier in generation_ids:
                    result["halt"] = True
                    raise ValueError("duplicate_generation_id")
                generation_ids.add(identifier)
            result["http_status"] = response.status_code
            if response.status_code != 200:
                result["halt"] = True
                raise ValueError("http_error")
            normalized = validate_response(raw, job["request"])
            result.update(normalized)
            result["status"] = "completed"
        except httpx.HTTPError:
            result.update(error="transport_uncertain", halt=True)
        except (ValueError, KeyError, TypeError) as exc:
            # Only local validation codes are logged, never request headers or exception URLs.
            result["error"] = str(exc) if type(exc) is ValueError else "invalid_schema"
            if result["cost_usd"] is None or result["error"] in {"wrong_model", "wrong_provider"}:
                result["halt"] = True
        result["latency_seconds"] = time.monotonic() - started
        if result["cost_usd"] is not None:
            spent += result["cost_usd"] - reservation
        halted = halted or result.get("halt", False)
        append_event(ledger, result)
        outcomes[job["job_id"]] = result

    try:
        jobs = [job for job in manifest["jobs"] if job["job_id"] not in reservations]
        while jobs and len(reservations) < limit and not halted:
            tasks: list[Coroutine[Any, Any, None]] = []
            while jobs and len(tasks) < concurrency and len(reservations) < limit:
                job = jobs.pop(0)
                reservation = (len(encode(job["request"])) + 3000) * float(
                    OPENROUTER_ROUTE["input_rate_usd"]
                )
                if spent + reservation > manifest["dollar_cap"]:
                    jobs.clear()
                    break
                event = {
                    "event": "reserve",
                    "job_id": job["job_id"],
                    "reserved_usd": reservation,
                    "attempt": len(reservations) + 1,
                }
                append_event(ledger, event)
                reservations[job["job_id"]] = event
                spent += reservation
                tasks.append(attempt(job, reservation, event["attempt"]))
            if not tasks:
                break
            await asyncio.gather(*tasks)
    finally:
        if own_client:
            await client.aclose()
    ordered = [outcomes[key] for key in reservations if key in outcomes]
    durable_write(output / "results.jsonl", b"".join(encode(row) + b"\n" for row in ordered))
    summary = {
        "attempts": len(reservations),
        "completed": sum(row["status"] == "completed" for row in ordered),
        "failed": sum(row["status"] != "completed" for row in ordered),
        "pending": len(reservations) - len(outcomes),
        "cost_or_reserved_usd": spent,
        "halted": halted,
    }
    durable_write(output / "summary.json", encode(summary) + b"\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.manifest, args.output, args.limit, args.concurrency))))


if __name__ == "__main__":
    main()
