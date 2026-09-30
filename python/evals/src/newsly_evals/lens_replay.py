"""Prepare file-only, final-text news category replay inputs for the Rust driver.

The input is a bounded read-only operator export. This module never connects to a
database, embeds text, generates category names, or implements clustering policy.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def clean_text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def embedding_input(row: dict[str, Any]) -> tuple[str, str]:
    """Mirror the documented encoder-v1 text serialization, not routing policy."""
    summary = clean_text(row.get("summary_text"))
    title = next(
        (
            text
            for key in ("summary_title", "cluster_title", "article_title")
            if (text := clean_text(row.get(key)))
        ),
        summary[:120] or f"News item {row['id']}",
    )
    points = [
        text
        for point in row.get("key_points", [])
        if (text := clean_text(point.get("text") if isinstance(point, dict) else point))
    ]
    return title, "\n".join(part for part in (title, summary, " ".join(points)) if part)


def operator_rows(path: Path) -> list[dict[str, Any]]:
    """Read concatenated JSON operator responses, rejecting partial exports."""
    source = path.read_text()
    decoder = json.JSONDecoder()
    cursor = 0
    rows: list[dict[str, Any]] = []
    while cursor < len(source):
        if source[cursor].isspace():
            cursor += 1
            continue
        response, cursor = decoder.raw_decode(source, cursor)
        if not response.get("ok") or response["data"].get("truncated"):
            raise ValueError("Failed or truncated operator export")
        rows.extend(response["data"]["rows"])
    if not rows:
        raise ValueError("Empty operator export")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate source IDs in operator export")
    return rows


def normalize_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for row in rows:
        title, text = embedding_input(row)
        actual_hash = hashlib.sha256(text.encode()).hexdigest()
        if actual_hash != row["input_hash"]:
            excluded.append({"id": row["id"], "reason": "current_input_hash_mismatch"})
            continue
        if row["model"] != "qwen/qwen3-embedding-8b" or row["encoder_version"] != 1:
            raise ValueError("Mixed model or encoder version")
        raw = base64.b64decode(row["vector_f32_base64"])
        dimensions = row["dimensions"]
        if dimensions != 4096 or len(raw) != dimensions * 4:
            raise ValueError("Invalid vector dimensions")
        vector = list(struct.unpack(f">{dimensions}f", raw))
        if not all(math.isfinite(v) for v in vector) or not any(vector):
            raise ValueError("Invalid vector values")
        records.append(
            {
                **{k: v for k, v in row.items() if k != "vector_f32_base64"},
                "title": title,
                "event_id": row["id"],
                "vector": vector,
                "vector_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    records.sort(key=lambda item: (item["available_at"], item["id"]))
    manifest = {
        "schema_version": 1,
        "exported_rows": len(rows),
        "accepted_rows": len(records),
        "excluded": excluded,
        "model": "qwen/qwen3-embedding-8b",
        "dimensions": 4096,
        "encoder_version": 1,
        "vector_precision": "PostgreSQL float4send, IEEE754 big-endian float32",
        "platform_counts": dict(Counter(r["platform"] for r in records)),
        "week_counts": dict(
            sorted(
                Counter(
                    datetime.fromtimestamp(r["available_at"], UTC).strftime("%G-W%V")
                    for r in records
                ).items()
            )
        ),
        "candidate_api_calls": 0,
        "judge_api_calls": 0,
        "external_api_cost_usd": 0,
        "disclosures": [
            "Final-text counterfactual replay; current vectors and canonical survivors "
            "are not historical snapshots.",
            "Arrival order uses max(ingested_at, processed_at); "
            "no input after a cutoff enters fitting.",
            "Sample is source/week stratified among cached global representatives, "
            "not population-weighted or personalized.",
            "Canonical representative IDs are a deduplication proxy, "
            "not verified distinct-event labels.",
            "All unread retention is simulated; no historical read state or "
            "production lens names are reconstructed.",
            "Embedding-created timestamps cannot prove when an overwritten "
            "current vector first existed.",
        ],
    }
    return records, manifest


def variants(wide: bool = True) -> list[dict[str, Any]]:
    base: dict[str, Any] = {
        "algorithm": "rolling_spherical_kmeans",
        "lookback_days": 14,
        "k": 10,
        "warm_start": True,
        "seed": 0,
        "max_iterations": 20,
        "min_cluster_size": 3,
        "identity_similarity": 0.7,
    }
    configs = [
        {**base, "label": "frozen", "algorithm": "frozen_initial"},
        {**base, "label": "incremental_surrogate", "algorithm": "incremental_surrogate"},
        {**base, "label": "warm14"},
        {**base, "label": "warm7", "lookback_days": 7},
        {**base, "label": "warm14_decay7", "decay_half_life_days": 7},
        {**base, "label": "warm14_noise50", "confidence_threshold": 0.5},
    ]
    if wide:
        configs.extend(
            [
                {**base, "label": "warm28", "lookback_days": 28},
                {**base, "label": "warm14_k6", "k": 6},
                {**base, "label": "warm14_k14", "k": 14},
                {**base, "label": "novelty", "algorithm": "novelty_aware"},
            ]
        )
        configs.extend(
            {**base, "label": f"cold14_seed{seed}", "warm_start": False, "seed": seed}
            for seed in range(5)
        )
    return configs


def checkpoints(cadence: str, timezone: str = "America/Los_Angeles") -> list[int]:
    """Local 03:00 experiment instants; production DST policy is in the plan."""
    zone = ZoneInfo(timezone)
    start = datetime(2026, 8, 10, 3, tzinfo=zone)
    end = datetime(2026, 9, 26, 3, tzinfo=zone)
    step = timedelta(days=7 if cadence == "weekly" else 1)
    values = []
    current = start
    while current <= end:
        values.append(int(current.timestamp()))
        current += step
    # Keep the same final partial week in both arms.
    if values[-1] != int(end.timestamp()):
        values.append(int(end.timestamp()))
    return values


def make_request(
    records: list[dict[str, Any]],
    cohort: str,
    cadence: str,
    *,
    wide: bool = True,
) -> dict[str, Any]:
    platforms = {
        "all": None,
        "technology": {"hackernews", "techmeme"},
        "general": {"brutalist", "memeorandum", "mediagazer", "finurls"},
    }[cohort]
    items = [
        {key: row[key] for key in ("id", "available_at", "vector", "title", "event_id")}
        for row in records
        if platforms is None or row["platform"] in platforms
    ]
    return {
        "schema_version": 1,
        "items": items,
        "checkpoints": checkpoints(cadence),
        "variants": variants(wide),
        "max_items": 1500,
    }
