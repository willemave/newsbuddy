"""Derive OpenRouter inference runs while preserving frozen original judge evidence."""

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .artifacts import CRITERIA, digest, load_manifest, read_jsonl, write_json
from .judge import batches, prepare_judge
from .references import accepted_references
from .transport import OPENROUTER_PROVIDER, OPENROUTER_ROUTE

REQUIRED = ("manifest.json", "judge-checkpoint.json", "judge-prompts.json", "judge-labels.jsonl")
OPTIONAL = ("judge-repeat-labels.jsonl", "judge-repeat-audit.json")
ALLOCATIONS = {
    "smoke": {"kind": "smoke", "request_cap": 16, "dollar_cap": 0.01},
    "main": {"kind": "main", "request_cap": 8384, "dollar_cap": 4.99},
}


def source_inventory(root: Path) -> dict[str, str]:
    paths = [root / name for name in REQUIRED]
    if any(not path.is_file() for path in paths):
        raise ValueError("Reference source lacks a required frozen artifact")
    paths.extend(root / name for name in OPTIONAL if (root / name).is_file())
    paths.extend(path for path in (root / "judge-raw").rglob("*") if path.is_file())
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
    }


def _compare_labels(
    root: Path, filename: str, labels: list[dict[str, Any]], *, optional: bool = False
) -> None:
    path = root / filename
    if optional and not path.exists():
        if labels:
            raise ValueError("Repeat references lack their labels artifact")
        return
    exported = read_jsonl(path)
    if sorted(exported, key=lambda row: row["pair_id"]) != sorted(
        labels, key=lambda row: row["pair_id"]
    ):
        raise ValueError("Reference labels differ from verified saved responses")


def validate_source(root: Path, manifest: dict[str, Any], allocation: str) -> tuple[int, int]:
    if manifest["schema_version"] != 1:
        raise ValueError("Reference migration must start from an original schema-1 judge run")
    frozen = json.loads((root / "judge-prompts.json").read_text())
    expected = {
        "manifest_hash": manifest["manifest_hash"],
        "batches": batches(manifest),
        "audit_batches": batches(manifest, audit=True),
    }
    if frozen != expected:
        raise ValueError("Source canonical judge batches differ from their frozen prompts")
    state = json.loads((root / "judge-checkpoint.json").read_text())
    labels, _ = accepted_references(manifest, root, state)
    repeats, _ = accepted_references(manifest, root, state, audit=True)
    _compare_labels(root, "judge-labels.jsonl", labels)
    _compare_labels(root, "judge-repeat-labels.jsonl", repeats, optional=True)
    ids = [row["pair_id"] for row in labels]
    repeat_ids = [row["pair_id"] for row in repeats]
    if len(ids) != len(set(ids)) or len(repeat_ids) != len(set(repeat_ids)):
        raise ValueError("Reference source contains duplicate pair labels")
    if not labels:
        raise ValueError("Reference source has no accepted labels")
    if allocation == "main":
        wanted = {pair["pair_id"] for pair in manifest["pairs"]}
        wanted_repeats = {
            pair_id for batch in expected["audit_batches"] for pair_id in batch["pair_ids"]
        }
        if set(ids) != wanted or set(repeat_ids) != wanted_repeats:
            raise ValueError(
                "Main migration requires complete primary and repeat reference coverage"
            )
    return len(labels), len(repeats)


def migrated_job(old: dict[str, Any]) -> dict[str, Any]:
    new = copy.deepcopy(old)
    new["request"]["model"] = OPENROUTER_ROUTE["request_model"]
    new["request"]["provider"] = copy.deepcopy(OPENROUTER_PROVIDER)
    new["input_hash"] = digest(
        {
            "request": new["request"],
            "option_order": list(CRITERIA),
            "transport": OPENROUTER_ROUTE,
            "version": 2,
        }
    )
    return new


def validate_equivalence(old: dict[str, Any], new: dict[str, Any]) -> None:
    if old["pairs"] != new["pairs"]:
        raise ValueError("Migrated pair objects differ from the original frozen pairs")
    preserved = set(old) - {"schema_version", "manifest_hash", "model", "jobs"}
    if any(old[key] != new.get(key) for key in preserved):
        raise ValueError("Migration changed source/profile/pairing metadata")
    if len(old["jobs"]) != len(new["jobs"]) or any(
        migrated_job(before) != after
        for before, after in zip(old["jobs"], new["jobs"], strict=True)
    ):
        raise ValueError("Migration changed job order or fields beyond allowed transport changes")
    for audit in (False, True):
        if batches(old, audit=audit) != batches(new, audit=audit):
            raise ValueError("Migrated canonical judge batches are not equivalent")


def migrate_openrouter(source: Path, output: Path, allocation: str) -> dict[str, Any]:
    if allocation not in ALLOCATIONS:
        raise ValueError("Allocation must be smoke or main")
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ValueError("Migration cannot overwrite the source run")
    original = load_manifest(source / "manifest.json")
    primary_count, audit_count = validate_source(source, original, allocation)
    descriptor: dict[str, Any] = {
        "schema_version": 1,
        "path": os.path.relpath(source, output),
        "manifest_hash": original["manifest_hash"],
        "files": source_inventory(source),
        "primary_labels": primary_count,
        "audit_labels": audit_count,
    }
    descriptor["descriptor_hash"] = digest(descriptor)
    body = copy.deepcopy({key: value for key, value in original.items() if key != "manifest_hash"})
    body.update(
        {
            "schema_version": 2,
            "model": OPENROUTER_ROUTE["request_model"],
            "transport": copy.deepcopy(OPENROUTER_ROUTE),
            "allocation": copy.deepcopy(ALLOCATIONS[allocation]),
            "reference_source": descriptor,
            "jobs": [migrated_job(job) for job in original["jobs"]],
        }
    )
    manifest = {**body, "manifest_hash": digest(body)}
    validate_equivalence(original, manifest)
    target = output / "manifest.json"
    if target.exists():
        if load_manifest(target) != manifest:
            raise ValueError("Output contains a different immutable migration")
    elif output.exists() and any(output.iterdir()):
        raise ValueError("Migration output must be new or contain the identical derived manifest")
    # No source writes, judge calls, or duplicate human worksheets.
    write_json(target, manifest)
    prepare_judge(manifest, output)
    resolve_reference_source(manifest, output)
    return manifest


def resolve_reference_source(
    manifest: dict[str, Any], output: Path
) -> tuple[Path, dict[str, Any], dict[str, Any] | None]:
    descriptor = manifest.get("reference_source")
    if descriptor is None:
        if manifest["schema_version"] != 1:
            raise ValueError("Schema-2 inference run requires a frozen reference source")
        return output, manifest, None
    if manifest["schema_version"] != 2 or descriptor.get("schema_version") != 1:
        raise ValueError("Unsupported reference-source schema")
    unhashed = {key: value for key, value in descriptor.items() if key != "descriptor_hash"}
    if descriptor.get("descriptor_hash") != digest(unhashed):
        raise ValueError("Reference-source descriptor hash mismatch")
    relative = Path(descriptor["path"])
    if relative.is_absolute():
        raise ValueError("Reference source must be relative to the new run")
    root = (output / relative).resolve()
    if root == output.resolve() or source_inventory(root) != descriptor["files"]:
        raise ValueError("Reference source artifact contents or exact inventory changed")
    original = load_manifest(root / "manifest.json")
    if original["manifest_hash"] != descriptor["manifest_hash"]:
        raise ValueError("Reference source manifest hash differs")
    if (
        manifest.get("transport") != OPENROUTER_ROUTE
        or manifest["model"] != OPENROUTER_ROUTE["request_model"]
    ):
        raise ValueError("Migrated transport/model differs from the frozen route")
    allocation = manifest.get("allocation", {})
    kind = allocation.get("kind")
    if kind not in ALLOCATIONS or allocation != ALLOCATIONS[kind]:
        raise ValueError("Migrated allocation differs from its fixed experiment allowance")
    validate_equivalence(original, manifest)
    counts = validate_source(root, original, kind)
    if counts != (descriptor["primary_labels"], descriptor["audit_labels"]):
        raise ValueError("Reference source label coverage differs")
    provenance = {
        "source_manifest_hash": original["manifest_hash"],
        "descriptor_hash": descriptor["descriptor_hash"],
        "path": descriptor["path"],
        "primary_labels": counts[0],
        "audit_labels": counts[1],
    }
    return root, original, provenance
