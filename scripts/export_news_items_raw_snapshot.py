"""Export raw news item rows for prompt/eval inspection.

This is intentionally a standalone script rather than an admin CLI command. Run
it inside the app runtime that has database and object-storage access when raw
article bodies need to be dereferenced.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.settings import get_settings
from app.models.contracts import NewsItemStatus
from app.models.db import NewsItem
from app.services.news_article_bodies import get_news_item_article_body_resolver

SNAPSHOT_COMMAND = "export.news-items-raw-snapshot"
NEWS_ITEM_RAW_SNAPSHOT_COLUMNS = [
    "id",
    "ingest_key",
    "visibility_scope",
    "owner_user_id",
    "platform",
    "source_type",
    "source_label",
    "source_external_id",
    "user_scraper_config_id",
    "user_integration_connection_id",
    "canonical_item_url",
    "canonical_story_url",
    "article_url",
    "article_domain",
    "discussion_url",
    "summary_key_points",
    "summary_text",
    "raw_metadata",
    "status",
    "legacy_content_id",
    "representative_news_item_id",
    "cluster_size",
    "enrichment_updated_at",
    "published_at",
    "ingested_at",
    "processed_at",
    "created_at",
    "updated_at",
    "article_title",
    "summary_title",
    "article_body_text",
    "article_body_source",
    "article_body_updated_at",
    "article_body_error",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export newest raw news_items with live summaries and article bodies"
    )
    parser.add_argument("--limit", type=int, default=150, help="Maximum rows to export")
    parser.add_argument(
        "--status",
        dest="statuses",
        action="append",
        default=None,
        help="News item status to include. Repeat or comma-separate; use 'all' for all.",
    )
    parser.add_argument(
        "--no-article-body",
        action="store_true",
        help="Skip resolving article body text from object storage/content records",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Optional JSON file path. Without this, the snapshot is printed to stdout.",
    )
    return parser.parse_args(argv)


def build_news_items_raw_snapshot(
    *,
    database_url: str | None = None,
    limit: int = 150,
    statuses: list[str] | None = None,
    include_article_body: bool = True,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least 1")

    selected_statuses = normalize_news_item_statuses(statuses)
    engine = create_engine(database_url or resolve_runtime_database_url(), pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            query = session.query(NewsItem)
            if selected_statuses:
                query = query.filter(NewsItem.status.in_(selected_statuses))
            items = (
                query.order_by(NewsItem.ingested_at.desc().nullslast(), NewsItem.id.desc())
                .limit(limit)
                .all()
            )

            resolver = get_news_item_article_body_resolver() if include_article_body else None
            rows: list[dict[str, Any]] = []
            body_resolved = 0
            body_missing = 0
            body_errors = 0
            for item in items:
                row = serialize_news_item_raw_snapshot_row(item)
                if resolver is not None:
                    try:
                        resolved_body = resolver.resolve(session, news_item=item)
                    except Exception as exc:  # noqa: BLE001
                        resolved_body = None
                        row["article_body_error"] = str(exc)
                        body_errors += 1

                    if resolved_body is not None and resolved_body.text:
                        row["article_body_text"] = resolved_body.text
                        row["article_body_source"] = resolved_body.source
                        row["article_body_updated_at"] = serialize_datetime(
                            resolved_body.updated_at
                        )
                        body_resolved += 1
                    else:
                        body_missing += 1
                rows.append(row)

            return {
                "limit": limit,
                "columns": list(NEWS_ITEM_RAW_SNAPSHOT_COLUMNS),
                "row_count": len(rows),
                "rows": rows,
                "statuses": selected_statuses or ["all"],
                "include_article_body": include_article_body,
                "body_stats": {
                    "resolved": body_resolved,
                    "missing": body_missing,
                    "errors": body_errors,
                },
                "redacted": False,
                "truncated": len(rows) >= limit,
            }
    finally:
        engine.dispose()


def normalize_news_item_statuses(statuses: list[str] | None) -> list[str]:
    if not statuses:
        return [NewsItemStatus.READY.value]

    normalized: list[str] = []
    for raw_status in statuses:
        for status_part in str(raw_status).split(","):
            status = status_part.strip().lower()
            if not status:
                continue
            if status == "all":
                return []
            normalized.append(status)

    if not normalized:
        return [NewsItemStatus.READY.value]

    allowed_statuses = {status.value for status in NewsItemStatus}
    unknown_statuses = sorted(set(normalized) - allowed_statuses)
    if unknown_statuses:
        raise ValueError(f"Unknown news item statuses: {', '.join(unknown_statuses)}")
    return normalized


def resolve_runtime_database_url() -> str:
    configured_url = str(get_settings().database_url)
    if configured_url and "change-me" not in configured_url:
        return configured_url

    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        return configured_url

    user = quote(os.environ.get("POSTGRES_USER", "newsly"), safe="")
    database = quote(os.environ.get("POSTGRES_DB", "newsly"), safe="")
    port = os.environ.get("POSTGRES_PORT", "5432")
    quoted_password = quote(password, safe="")
    return f"postgresql+psycopg://{user}:{quoted_password}@127.0.0.1:{port}/{database}"


def serialize_news_item_raw_snapshot_row(item: NewsItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "ingest_key": item.ingest_key,
        "visibility_scope": item.visibility_scope,
        "owner_user_id": item.owner_user_id,
        "platform": item.platform,
        "source_type": item.source_type,
        "source_label": item.source_label,
        "source_external_id": item.source_external_id,
        "user_scraper_config_id": item.user_scraper_config_id,
        "user_integration_connection_id": item.user_integration_connection_id,
        "canonical_item_url": item.canonical_item_url,
        "canonical_story_url": item.canonical_story_url,
        "article_url": item.article_url,
        "article_domain": item.article_domain,
        "discussion_url": item.discussion_url,
        "summary_key_points": item.summary_key_points,
        "summary_text": item.summary_text,
        "raw_metadata": item.raw_metadata,
        "status": item.status,
        "legacy_content_id": item.legacy_content_id,
        "representative_news_item_id": item.representative_news_item_id,
        "cluster_size": item.cluster_size,
        "enrichment_updated_at": serialize_datetime(item.enrichment_updated_at),
        "published_at": serialize_datetime(item.published_at),
        "ingested_at": serialize_datetime(item.ingested_at),
        "processed_at": serialize_datetime(item.processed_at),
        "created_at": serialize_datetime(item.created_at),
        "updated_at": serialize_datetime(item.updated_at),
        "article_title": item.article_title,
        "summary_title": item.summary_title,
        "article_body_text": None,
        "article_body_source": None,
        "article_body_updated_at": None,
        "article_body_error": None,
    }


def serialize_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def build_envelope(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "command": SNAPSHOT_COMMAND,
        "data": snapshot,
    }


def write_envelope(envelope: dict[str, Any], output_file: Path | None) -> None:
    encoded = json.dumps(envelope, ensure_ascii=False, indent=2, default=str) + "\n"
    if output_file is None:
        sys.stdout.write(encoded)
        return
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(encoded, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        snapshot = build_news_items_raw_snapshot(
            limit=args.limit,
            statuses=args.statuses,
            include_article_body=not args.no_article_body,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    envelope = build_envelope(snapshot)
    write_envelope(envelope, args.output_file)
    if args.output_file is not None:
        body_stats = snapshot["body_stats"]
        print(
            "Exported "
            f"{snapshot['row_count']} news items to {args.output_file} "
            f"({body_stats['resolved']} article bodies resolved, "
            f"{body_stats['missing']} missing, {body_stats['errors']} errors).",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
