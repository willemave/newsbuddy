"""Bind accepted reference labels to the exact stored judge attempt evidence."""

import json
from pathlib import Path
from typing import Any

from .artifacts import digest
from .judge import REFERENCE_VALIDATOR_VERSION, batches, validate_labels


def accepted_references(
    manifest: dict[str, Any], output: Path, state: dict[str, Any], *, audit: bool = False
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if state["manifest_hash"] != manifest["manifest_hash"]:
        raise ValueError("Judge checkpoint manifest mismatch")
    expected = {batch["batch_id"]: batch for batch in batches(manifest, audit=audit)}
    pairs = {pair["pair_id"]: pair for pair in manifest["pairs"]}
    labels: list[dict[str, Any]] = []
    provenance = []
    for batch_id, result in state["audit_results" if audit else "results"].items():
        if batch_id not in expected:
            raise ValueError("Unrequested judge batch")
        completed = [
            (index, attempt)
            for index, attempt in enumerate(state["attempts"])
            if attempt["batch_id"] == batch_id
            and attempt["status"] == "complete"
            and bool(attempt.get("audit", False)) == audit
        ]
        promoted = [
            record
            for record in state.get("validation_promotions", [])
            if record["batch_id"] == batch_id
        ]
        if len(completed) + len(promoted) != 1:
            raise ValueError("Accepted judge result must bind to one completed or promoted attempt")
        if promoted:
            record = promoted[0]
            index = record["attempt_index"]
            if not isinstance(index, int) or not 0 <= index < len(state["attempts"]):
                raise ValueError("Promotion attempt index is invalid")
            attempt = state["attempts"][index]
            if (
                attempt["batch_id"] != batch_id
                or not attempt.get("halt")
                or bool(attempt.get("audit", False)) != audit
            ):
                raise ValueError("Promotion does not reference the failed reference attempt")
            if record["validator_version"] != REFERENCE_VALIDATOR_VERSION:
                raise ValueError("Promotion validator version is not supported")
            raw_path = record["raw_path"]
            if raw_path != attempt.get("raw_path", "judge-raw/" + batch_id + ".json"):
                raise ValueError("Promotion raw path differs from its failed attempt")
            expected_hash = record["raw_hash"]
            provenance_type = "promoted"
        else:
            index, attempt = completed[0]
            raw_path = attempt.get("raw_path", "judge-raw/" + batch_id + ".json")
            expected_hash = attempt.get("raw_hash")
            provenance_type = "completed"
        relative = Path(raw_path)
        if relative.is_absolute() or relative.parent != Path("judge-raw"):
            raise ValueError("Judge raw-response path is invalid")
        raw = json.loads((output / relative).read_text())
        observed_hash = digest(raw)
        if expected_hash is not None and observed_hash != expected_hash:
            raise ValueError("Judge raw-response hash differs from its attempt")
        validated = validate_labels(raw, expected[batch_id], pairs)
        if sorted(validated, key=lambda row: row["pair_id"]) != sorted(
            result["labels"], key=lambda row: row["pair_id"]
        ):
            raise ValueError("Accepted judge labels differ from their raw response")
        if result["model_usage"] != raw["modelUsage"]:
            raise ValueError("Accepted judge model usage differs from its raw response")
        labels.extend(validated)
        provenance.append(
            {
                "batch_id": batch_id,
                "attempt_index": index,
                "kind": provenance_type,
                "raw_path": raw_path,
                "observed_raw_hash": observed_hash,
                "attempt_recorded_raw_hash": expected_hash is not None,
            }
        )
    return labels, {
        "batches": provenance,
        "legacy_completed_without_recorded_hash": sum(
            not row["attempt_recorded_raw_hash"] for row in provenance
        ),
        "note": "Legacy completed attempts are bound by saved raw labels/model usage; "
        "their observed hashes were not recorded at original completion.",
    }
