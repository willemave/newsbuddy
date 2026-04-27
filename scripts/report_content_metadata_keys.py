#!/usr/bin/env python3
"""Report observed `content_metadata` keys for migration cleanup."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.schema import Content

KNOWN_TOP_LEVEL_KEYS: set[str] = {
    "aggregator",
    "all_detected_feeds",
    "article",
    "audio_url",
    "author",
    "canonical_content_id",
    "comment_count",
    "content",
    "content_to_summarize",
    "content_type",
    "detected_feed",
    "discussion_url",
    "domain",
    "error",
    "error_type",
    "excerpt",
    "extraction_error",
    "extraction_failed",
    "extraction_failure_details",
    "feed_subscription",
    "final_url",
    "final_url_after_redirects",
    "gate_page_reason",
    "has_transcript",
    "has_video",
    "image_generated_at",
    "image_url",
    "internal_urls",
    "platform",
    "processing",
    "processing_errors",
    "publication_date",
    "share_and_chat_pending",
    "share_and_chat_user_ids",
    "source",
    "source_type",
    "submitted_by_user_id",
    "submitted_via",
    "subscribe_to_feed",
    "summary",
    "summary_key_points",
    "summary_kind",
    "summary_version",
    "summarization_input_fingerprint",
    "thumbnail_url",
    "top_comment",
    "transcript",
    "tweet_id",
    "tweet_video_skip_reason",
    "url",
    "used_firecrawl_fallback",
    "video_duration_ms",
    "video_transcript",
    "youtube_equivalent_resolution",
}

KNOWN_PROCESSING_KEYS: set[str] = {
    "detected_feed",
    "image_generated_at",
    "image_url",
    "submitted_by_user_id",
    "submitted_via",
    "thumbnail_url",
    "workflow_from",
    "workflow_to",
    "workflow_transition",
}

API_READER_KEYS: set[str] = {
    "article",
    "aggregator",
    "comment_count",
    "detected_feed",
    "discussion_url",
    "image_generated_at",
    "image_url",
    "platform",
    "processing",
    "source",
    "submitted_by_user_id",
    "submitted_via",
    "summary",
    "summary_key_points",
    "summary_kind",
    "summary_version",
    "thumbnail_url",
    "top_comment",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sample content_metadata and report unknown top-level/processing keys."
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--content-type", default=None)
    parser.add_argument("--include-known", action="store_true")
    args = parser.parse_args()

    with get_db() as db:
        report = build_report(
            db,
            limit=max(int(args.limit), 1),
            content_type=args.content_type,
            include_known=bool(args.include_known),
        )

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


def build_report(
    db: Session,
    *,
    limit: int,
    content_type: str | None,
    include_known: bool = False,
) -> dict[str, Any]:
    query = db.query(Content.id, Content.content_type, Content.content_metadata).order_by(
        Content.id.desc()
    )
    if content_type:
        query = query.filter(Content.content_type == content_type)
    rows = query.limit(limit).all()

    top_level_counter: Counter[str] = Counter()
    processing_counter: Counter[str] = Counter()
    api_reader_counter: Counter[str] = Counter()
    unknown_top_level_examples: dict[str, list[int]] = {}
    unknown_processing_examples: dict[str, list[int]] = {}

    for content_id, _content_type, metadata in rows:
        if not isinstance(metadata, dict):
            continue
        top_level_counter.update(str(key) for key in metadata)
        for key in metadata:
            key_text = str(key)
            if key_text in API_READER_KEYS:
                api_reader_counter[key_text] += 1
            if key_text not in KNOWN_TOP_LEVEL_KEYS:
                _append_example(unknown_top_level_examples, key_text, int(content_id))

        processing = metadata.get("processing")
        if isinstance(processing, dict):
            processing_counter.update(str(key) for key in processing)
            for key in processing:
                key_text = str(key)
                if key_text not in KNOWN_PROCESSING_KEYS:
                    _append_example(unknown_processing_examples, key_text, int(content_id))

    report: dict[str, Any] = {
        "sampled_rows": len(rows),
        "content_type": content_type,
        "unknown_top_level_keys": _counter_payload(
            top_level_counter,
            known_keys=KNOWN_TOP_LEVEL_KEYS,
            examples=unknown_top_level_examples,
        ),
        "unknown_processing_keys": _counter_payload(
            processing_counter,
            known_keys=KNOWN_PROCESSING_KEYS,
            examples=unknown_processing_examples,
        ),
        "api_reader_keys_seen": dict(sorted(api_reader_counter.items())),
    }
    if include_known:
        report["top_level_keys_seen"] = dict(sorted(top_level_counter.items()))
        report["processing_keys_seen"] = dict(sorted(processing_counter.items()))
    return report


def _counter_payload(
    counter: Counter[str],
    *,
    known_keys: set[str],
    examples: dict[str, list[int]],
) -> list[dict[str, Any]]:
    rows = []
    for key, count in sorted(counter.items()):
        if key in known_keys:
            continue
        rows.append({"key": key, "count": count, "example_content_ids": examples.get(key, [])})
    return rows


def _append_example(examples: dict[str, list[int]], key: str, content_id: int) -> None:
    bucket = examples.setdefault(key, [])
    if len(bucket) < 5 and content_id not in bucket:
        bucket.append(content_id)


if __name__ == "__main__":
    raise SystemExit(main())
