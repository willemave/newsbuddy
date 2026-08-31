"""Versioned, deterministic artifact helpers for offline eval pipelines."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

ARTIFACT_SCHEMA_VERSION = 1
TITLE_CLUSTERING_ROW_ARTIFACT = "newsly.title_clustering.row"
TITLE_CLUSTERING_MANIFEST_ARTIFACT = "newsly.title_clustering.manifest"
TITLE_CLUSTERING_TITLE_ROW_ARTIFACT = "newsly.title_clustering.title_row"

_ARTIFACT_FIELDS = frozenset({"artifact_type", "schema_version"})


def read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    """Read object records from JSONL, accepting legacy unversioned snapshots."""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {error.msg}") from error
            if not isinstance(value, dict):
                raise ValueError(f"expected an object at {path}:{line_number}")
            schema_version = value.get("schema_version")
            if schema_version is not None and schema_version != ARTIFACT_SCHEMA_VERSION:
                raise ValueError(
                    f"unsupported artifact schema_version {schema_version!r} "
                    f"at {path}:{line_number}"
                )
            records.append(
                {key: item for key, item in value.items() if key not in _ARTIFACT_FIELDS}
            )
    return records


def write_jsonl_artifact(
    path: Path,
    records: Iterable[Mapping[str, Any]],
    *,
    artifact_type: str,
) -> str:
    """Write stable versioned JSONL and return the SHA-256 of its exact bytes."""
    if not artifact_type.strip():
        raise ValueError("artifact_type must not be blank")
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with path.open("wb") as handle:
        for record in records:
            payload = {
                "schema_version": ARTIFACT_SCHEMA_VERSION,
                "artifact_type": artifact_type,
                **dict(record),
            }
            encoded = (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                + "\n"
            ).encode()
            handle.write(encoded)
            digest.update(encoded)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_artifact(
    path: Path,
    payload: Mapping[str, Any],
    *,
    artifact_type: str,
) -> None:
    if not artifact_type.strip():
        raise ValueError("artifact_type must not be blank")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_type": artifact_type,
        **dict(payload),
    }
    path.write_text(
        json.dumps(encoded, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def normalize_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def first_text(record: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = normalize_text(record.get(key))
        if value:
            return value
    return None
