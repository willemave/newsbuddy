"""Bounded Jev execution with durable reservations before every network attempt."""

import asyncio
import contextlib
import fcntl
import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from .artifacts import LABELS
from .ledger import append_event, initialize_ledger, persist_raw
from .transport import OPENROUTER_ROUTE, reported_cost, validate_transport

INPUT_RATE = 0.042 / 1_000_000
ENDPOINT = str(OPENROUTER_ROUTE["endpoint"])
RESPONSE_VALIDATOR_VERSION = "choice-cent-rounding-v2"


class ChoiceMismatch(ValueError):
    """A fully metered/routed response whose selected class contradicts its scores."""

    def __init__(self, normalized: dict[str, Any]) -> None:
        super().__init__("Selected choice is not an argmax")
        self.normalized = normalized


def validate_response(raw: dict[str, Any], model: str) -> dict[str, Any]:
    routed = model == OPENROUTER_ROUTE["request_model"]
    expected_model = OPENROUTER_ROUTE["response_model"] if routed else model
    if not isinstance(raw, dict) or raw.get("model") != expected_model:
        raise ValueError("Returned model differs from pinned model")
    if not isinstance(raw.get("answers"), dict):
        raise ValueError("Invalid answers schema")
    answer = raw["answers"]["filter"]
    if not isinstance(answer, dict) or not isinstance(answer.get("probabilities"), dict):
        raise ValueError("Invalid choice schema")
    probabilities = answer["probabilities"]
    if answer.get("type") != "choice" or set(probabilities) != set(LABELS):
        raise ValueError("Invalid choice schema")
    if any(
        isinstance(v, bool)
        or not isinstance(v, (int, float))
        or not math.isfinite(v)
        or not 0 <= v <= 1
        for v in probabilities.values()
    ):
        raise ValueError("Probabilities must be finite and within [0,1]")
    sum_error = abs(sum(probabilities.values()) - 1)
    cent_rounded = routed and all(
        abs(value - round(value, 2)) < 1e-12 for value in probabilities.values()
    )
    # Jev's live API emits probabilities at two-decimal precision. Three rounded
    # classes can total 0.99 or 1.01. Preserve the reported values for thresholding.
    tolerance = 0.010000000001 if cent_rounded else 0.001
    if sum_error > tolerance:
        raise ValueError("Probabilities do not sum to one")
    choice = answer["choice"]
    if choice not in LABELS:
        raise ValueError("Unknown selected choice")
    for key in ("input_tokens", "output_tokens"):
        value = raw["usage"][key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Missing/invalid metered usage")
    if routed:
        if raw.get("provider") != OPENROUTER_ROUTE["provider"]:
            raise ValueError("Returned provider differs from pinned provider")
        if not isinstance(raw.get("id"), str) or not raw["id"].strip():
            raise ValueError("Missing OpenRouter generation ID")
        reported_cost(raw["usage"])
    normalized = {
        "choice": choice,
        "probabilities": probabilities,
        "model": raw["model"],
        "usage": raw["usage"],
    }
    if routed:
        normalized.update({"generation_id": raw["id"], "provider": raw["provider"]})
    if probabilities[choice] < max(probabilities.values()) - 1e-8:
        raise ChoiceMismatch(normalized)
    return normalized


def validate_stored_result(result: dict[str, Any], model: str) -> None:
    normalized = validate_response(result["raw_response"], model)
    if any(result.get(key) != value for key, value in normalized.items()):
        raise ValueError("Stored result differs from its validated provider response")


def reserve_tokens(request: dict[str, Any]) -> int:
    # A conservative byte bound plus serialization overhead, not a tokenizer estimate.
    return len(json.dumps(request, ensure_ascii=False).encode()) + 4096


async def run_jev(
    manifest: dict[str, Any],
    output: Path,
    *,
    limit: int | None = None,
    dollar_cap: float,
    request_cap: int,
    concurrency: int = 4,
) -> dict[str, Any]:
    validate_transport(manifest)
    if (
        not 1 <= concurrency <= 4
        or not math.isfinite(dollar_cap)
        or not 0 < dollar_cap <= 5
        or not 1 <= request_cap <= 8400
    ):
        raise ValueError("Invalid limits")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    allocation = manifest["allocation"]
    if allocation["request_cap"] != request_cap or allocation["dollar_cap"] != dollar_cap:
        raise ValueError("Caps differ from frozen run allocation")
    judge_path = output / "judge-prompts.json"
    if (
        not judge_path.exists()
        or json.loads(judge_path.read_text()).get("manifest_hash") != manifest["manifest_hash"]
    ):
        raise ValueError("Freeze judge prompts before any Jev calls with prepare-judge")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY is required")
    output.mkdir(parents=True, exist_ok=True)
    # The file lock prevents two CLI invocations from independently spending one budget.
    with (output / "jev.lock").open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return await _run_locked(manifest, output, key, limit, dollar_cap, request_cap, concurrency)


async def _run_locked(
    manifest: dict[str, Any],
    output: Path,
    key: str,
    limit: int | None,
    dollar_cap: float,
    request_cap: int,
    concurrency: int,
) -> dict[str, Any]:
    state = initialize_ledger(output, manifest, dollar_cap, request_cap)
    if any(attempt.get("halt") for attempt in state["attempts"]):
        raise ValueError("Run halted on unsafe usage/protocol result; inspect before a new run")
    jobs = manifest["jobs"]
    expected = {j["job_id"]: j["input_hash"] for j in jobs}
    if any(
        job_id not in expected or result["input_hash"] != expected[job_id]
        for job_id, result in state["results"].items()
    ):
        raise ValueError("Checkpoint job identity mismatch")
    for result in state["results"].values():
        validate_stored_result(result, manifest["model"])
    todo = [j for j in jobs if j["job_id"] not in state["results"]]
    if limit is not None:
        todo = todo[:limit]
    accounted_cost = sum(a.get("actual_cost", a["reserved_cost"]) for a in state["attempts"])
    attempts_by_job: dict[str, int] = {}
    generation_ids = {r["generation_id"] for r in state["results"].values()}
    for prior_attempt in state["attempts"]:
        if prior_attempt.get("status") == "invalid_choice" or prior_attempt.get(
            "protocol_acknowledgement"
        ):
            wrapper = json.loads((output / prior_attempt["raw_path"]).read_text())
            generation_ids.add(wrapper["raw_response"]["id"])
    for attempt in state["attempts"]:
        attempts_by_job[attempt["job_id"]] = attempts_by_job.get(attempt["job_id"], 0) + 1
    mutex = asyncio.Lock()
    semaphore = asyncio.Semaphore(concurrency)
    stopped = asyncio.Event()

    async def execute(job: dict[str, Any], client: httpx.AsyncClient) -> None:
        nonlocal accounted_cost
        async with semaphore:
            prior = attempts_by_job.get(job["job_id"], 0)
            for attempt_number in range(prior, 3):
                async with mutex:
                    reserved = reserve_tokens(job["request"]) * INPUT_RATE
                    if stopped.is_set() or len(state["attempts"]) >= request_cap:
                        return
                    if accounted_cost + reserved > dollar_cap:
                        stopped.set()
                        return
                    attempt = {
                        "attempt_index": len(state["attempts"]),
                        "job_id": job["job_id"],
                        "input_hash": job["input_hash"],
                        "reserved_cost": reserved,
                        "reserved_tokens": reserve_tokens(job["request"]),
                        "status": "reserved",
                        "number": attempt_number,
                        "response_validator_version": RESPONSE_VALIDATOR_VERSION,
                        "retry_policy_version": "known-billed-choice-mismatch-v1",
                    }
                    append_event(
                        output,
                        {
                            "event": "reserve",
                            "attempt_index": attempt["attempt_index"],
                            "attempt": attempt,
                        },
                    )
                    state["attempts"].append(attempt)
                    accounted_cost += reserved
                started = time.monotonic()
                result = None
                outcome: dict[str, Any] = {}
                retry = False
                retry_delay = min(2**attempt_number, 4)
                try:
                    response = await client.post(ENDPOINT, json=job["request"])
                    outcome["http_status"] = response.status_code
                    if response.status_code in (429, 529):
                        # Unknown billing retains the reservation; never claim it was free.
                        outcome["status"] = "retryable_http"
                        retry = True
                        with contextlib.suppress(ValueError):
                            retry_delay = min(
                                max(float(response.headers.get("Retry-After", retry_delay)), 0), 30
                            )
                    elif not response.is_success:
                        outcome["status"] = "http_error"
                        if response.status_code in (400, 401, 402, 403, 404, 413, 422, 503):
                            outcome["halt"] = True
                            stopped.set()
                    else:
                        raw = response.json()
                        outcome.update(persist_raw(output, attempt, raw))
                        normalized = validate_response(raw, manifest["model"])
                        actual = reported_cost(normalized["usage"])
                        outcome["actual_cost"] = actual
                        if (
                            actual > reserved
                            or normalized["usage"]["input_tokens"] > attempt["reserved_tokens"]
                        ):
                            raise ValueError("Metered usage exceeded conservative reservation")
                        if normalized["generation_id"] in generation_ids:
                            raise ValueError("Duplicate OpenRouter generation ID")
                        generation_ids.add(normalized["generation_id"])
                        outcome["status"] = "complete"
                        result = {
                            **normalized,
                            "input_hash": job["input_hash"],
                            "latency_seconds": time.monotonic() - started,
                        }
                except ChoiceMismatch as exc:
                    # A known billed protocol failure may retry, but never becomes a decision.
                    actual = reported_cost(exc.normalized["usage"])
                    generation_id = exc.normalized.get("generation_id")
                    outcome.update(
                        {
                            "status": "invalid_choice",
                            "error_code": "choice_probability_mismatch",
                            "actual_cost": actual,
                        }
                    )
                    if (
                        actual > reserved
                        or exc.normalized["usage"]["input_tokens"] > attempt["reserved_tokens"]
                        or generation_id in generation_ids
                    ):
                        outcome["status"] = "invalid_response"
                        outcome["halt"] = True
                        stopped.set()
                    else:
                        generation_ids.add(generation_id)
                        retry = True
                except httpx.HTTPError:
                    outcome["status"] = "transport_error"
                except (ValueError, KeyError, TypeError):
                    outcome["status"] = "invalid_response"
                    outcome["halt"] = True
                    stopped.set()
                outcome["latency_seconds"] = time.monotonic() - started
                async with mutex:
                    append_event(
                        output,
                        {
                            "event": "finish",
                            "attempt_index": attempt["attempt_index"],
                            "outcome": outcome,
                            "result": result,
                        },
                    )
                    attempt.update(outcome)
                    accounted_cost += outcome.get("actual_cost", reserved) - reserved
                    if result is not None:
                        state["results"][job["job_id"]] = result
                if not retry or stopped.is_set():
                    return
                await asyncio.sleep(retry_delay)

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {key}"}, timeout=30, follow_redirects=False
    ) as client:
        await asyncio.gather(*(execute(j, client) for j in todo))
    attempt_counts = Counter(attempt["job_id"] for attempt in state["attempts"])
    exhausted = sorted(
        job_id
        for job_id, count in attempt_counts.items()
        if count >= 3 and job_id not in state["results"]
    )
    return {
        "completed": len(state["results"]),
        "exhausted_jobs": exhausted,
        "exhausted_job_count": len(exhausted),
        "attempts": len(state["attempts"]),
        "cost_or_reserved_usd": accounted_cost,
        "halted": stopped.is_set(),
    }
