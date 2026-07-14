#!/usr/bin/env python3
# ruff: noqa: E402
"""Measure Briefing presentation latency, query count, and payload size read-only."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import event, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.compression import BRIEFING_GZIP_COMPRESS_LEVEL
from app.core.db import get_engine, get_session_factory
from app.models.db import BriefingLens, BriefingSegment, BriefingState, User
from app.services.briefing.presentation import (
    BRIEFING_LENS_PAGE_MAX,
    get_briefing_index,
    get_briefing_index_validator,
    get_briefing_lens,
)


@dataclass(frozen=True)
class Sample:
    duration_ms: float
    query_count: int
    uncompressed_bytes: int
    compressed_bytes: int


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--lens-key")
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--page-limit", type=int, default=BRIEFING_LENS_PAGE_MAX)
    args = parser.parse_args()
    if args.iterations < 2:
        parser.error("--iterations must be at least 2")
    if not 1 <= args.page_limit <= BRIEFING_LENS_PAGE_MAX:
        parser.error(f"--page-limit must be between 1 and {BRIEFING_LENS_PAGE_MAX}")

    session_factory = get_session_factory()
    engine = get_engine()
    with session_factory() as db:
        _require_existing_read_state(db, user_id=args.user_id)
        lens_key = args.lens_key or _largest_lens_key(db, user_id=args.user_id)

    validator_samples = _measure(
        engine,
        iterations=args.iterations,
        operation=lambda db: get_briefing_index_validator(db, user_id=args.user_id),
    )
    index_samples = _measure(
        engine,
        iterations=args.iterations,
        operation=lambda db: get_briefing_index(db, user_id=args.user_id),
    )
    page_samples = _measure(
        engine,
        iterations=args.iterations,
        operation=lambda db: get_briefing_lens(
            db,
            user_id=args.user_id,
            lens_key=lens_key,
            limit=args.page_limit,
        ),
    )
    print(
        json.dumps(
            {
                "user_id": args.user_id,
                "lens_key": lens_key,
                "iterations": args.iterations,
                "page_limit": args.page_limit,
                "validator": _summary(validator_samples),
                "changed_index": _summary(index_samples),
                "first_page": _summary(page_samples),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _require_existing_read_state(db: Session, *, user_id: int) -> None:
    if db.get(User, user_id) is None:
        raise SystemExit(f"User {user_id} does not exist")
    if db.query(BriefingState.user_id).filter(BriefingState.user_id == user_id).first() is None:
        raise SystemExit(
            f"User {user_id} has no Briefing state; generate the performance fixture first"
        )


def _largest_lens_key(db: Session, *, user_id: int) -> str:
    row = (
        db.query(BriefingLens.key, func.count(BriefingSegment.id).label("segment_count"))
        .outerjoin(
            BriefingSegment,
            (BriefingSegment.lens_id == BriefingLens.id)
            & (BriefingSegment.status.in_(("active", "degraded"))),
        )
        .filter(BriefingLens.user_id == user_id, BriefingLens.status == "active")
        .group_by(BriefingLens.id, BriefingLens.key)
        .order_by(func.count(BriefingSegment.id).desc(), BriefingLens.position.asc())
        .first()
    )
    if row is None:
        raise SystemExit(f"User {user_id} has no active Briefing Lens")
    return str(row.key)


def _measure(
    engine: Engine,
    *,
    iterations: int,
    operation: Callable[[Session], Any],
) -> list[Sample]:
    session_factory = get_session_factory()
    samples: list[Sample] = []
    query_count = 0

    def count_query(
        _connection,
        _cursor,
        _statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(engine, "before_cursor_execute", count_query)
    try:
        for _ in range(iterations):
            query_count = 0
            with session_factory() as db:
                started_at = perf_counter()
                result = operation(db)
                serialized = _serialize(result)
                duration_ms = (perf_counter() - started_at) * 1_000
                db.rollback()
            compressed = gzip.compress(
                serialized,
                compresslevel=BRIEFING_GZIP_COMPRESS_LEVEL,
            )
            samples.append(
                Sample(
                    duration_ms=duration_ms,
                    query_count=query_count,
                    uncompressed_bytes=len(serialized),
                    compressed_bytes=len(compressed),
                )
            )
    finally:
        event.remove(engine, "before_cursor_execute", count_query)
    return samples


def _serialize(value: Any) -> bytes:
    if value is None:
        return b"null"
    model_dump_json = getattr(value, "model_dump_json", None)
    if callable(model_dump_json):
        return model_dump_json(by_alias=True).encode()
    return json.dumps(value.__dict__, default=str, sort_keys=True).encode()


def _summary(samples: list[Sample]) -> dict[str, float | int]:
    durations = sorted(sample.duration_ms for sample in samples)
    query_counts = [sample.query_count for sample in samples]
    uncompressed = [sample.uncompressed_bytes for sample in samples]
    compressed = [sample.compressed_bytes for sample in samples]
    return {
        "duration_p50_ms": round(_percentile(durations, 0.50), 2),
        "duration_p95_ms": round(_percentile(durations, 0.95), 2),
        "query_count_min": min(query_counts),
        "query_count_max": max(query_counts),
        "uncompressed_bytes_max": max(uncompressed),
        "compressed_bytes_max": max(compressed),
    }


def _percentile(sorted_values: list[float], percentile: float) -> float:
    index = max(math.ceil(len(sorted_values) * percentile) - 1, 0)
    return sorted_values[index]


if __name__ == "__main__":
    raise SystemExit(main())
