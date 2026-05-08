"""Export production-shaped news_items into a portable pipeline eval case."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.news_pipeline_eval_models import NewsPipelineEvalCase, NewsPipelineEvalUserContext

from app.core.db import get_db
from app.core.logging import get_logger, setup_logging
from app.models.schema import NewsItem
from app.models.user import User

logger = get_logger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a news pipeline eval case")
    parser.add_argument("--output", type=Path, required=True, help="Path to write the case JSON")
    parser.add_argument("--case-id", type=str, default=None, help="Optional explicit case id")
    parser.add_argument("--description", type=str, default=None, help="Optional case description")
    parser.add_argument("--user-id", type=int, default=None, help="Optional source user id")
    parser.add_argument(
        "--platform",
        action="append",
        dest="platforms",
        help="Optional platform filter (repeatable)",
    )
    parser.add_argument("--limit", type=int, default=150, help="Maximum rows to export")
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        help="Optional recency window in hours",
    )
    parser.add_argument(
        "--ingested-after",
        type=str,
        default=None,
        help="Optional ISO-8601 lower bound for ingested_at (inclusive)",
    )
    parser.add_argument(
        "--ingested-before",
        type=str,
        default=None,
        help="Optional ISO-8601 upper bound for ingested_at (exclusive)",
    )
    parser.add_argument(
        "--include-global",
        action="store_true",
        help="Export global news_items",
    )
    parser.add_argument(
        "--include-user-scoped",
        action="store_true",
        help="Export user-scoped news_items for --user-id",
    )
    parser.add_argument(
        "--ready-only",
        action="store_true",
        help="Restrict export to ready news_items only",
    )
    parser.add_argument(
        "--real-summaries-only",
        action="store_true",
        help="Restrict export to rows with non-empty summary_text or summary_key_points",
    )
    return parser.parse_args(argv)


def _parse_timestamp(raw_value: str | None) -> datetime | None:
    if raw_value is None:
        return None
    parsed = datetime.fromisoformat(raw_value)
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _real_summary_filter():
    return or_(
        func.trim(func.coalesce(NewsItem.summary_text, "")) != "",
        and_(
            func.json_type(NewsItem.summary_key_points) == "array",
            func.json_array_length(NewsItem.summary_key_points) > 0,
        ),
    )


def _serialize_news_item(item: NewsItem) -> dict[str, Any]:
    return {
        "legacy_content_id": item.legacy_content_id,
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
        "article_title": item.article_title,
        "article_domain": item.article_domain,
        "discussion_url": item.discussion_url,
        "summary_title": item.summary_title,
        "summary_key_points": (
            item.summary_key_points if isinstance(item.summary_key_points, list) else []
        ),
        "summary_text": item.summary_text,
        "raw_metadata": dict(item.raw_metadata or {}),
        "status": item.status,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "ingested_at": item.ingested_at.isoformat() if item.ingested_at else None,
    }


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _parse_args(argv)
    ingested_after = _parse_timestamp(args.ingested_after)
    ingested_before = _parse_timestamp(args.ingested_before)

    if args.include_user_scoped and args.user_id is None:
        raise SystemExit("--include-user-scoped requires --user-id")
    if args.hours is not None and (ingested_after is not None or ingested_before is not None):
        raise SystemExit("--hours cannot be combined with --ingested-after/--ingested-before")

    include_global = args.include_global
    include_user_scoped = args.include_user_scoped
    if not include_global and not include_user_scoped:
        include_global = True
        include_user_scoped = args.user_id is not None

    if not include_global and not include_user_scoped:
        raise SystemExit("At least one source scope must be exported")

    with get_db() as db:
        source_user = (
            db.query(User).filter(User.id == args.user_id).first() if args.user_id else None
        )
        query = db.query(NewsItem)
        filters = []
        if include_global:
            filters.append(NewsItem.visibility_scope == "global")
        if include_user_scoped and args.user_id is not None:
            filters.append(
                (NewsItem.visibility_scope == "user") & (NewsItem.owner_user_id == args.user_id)
            )
        query = query.filter(or_(*filters))
        if args.platforms:
            query = query.filter(NewsItem.platform.in_(args.platforms))
        if args.hours is not None:
            cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=args.hours)
            query = query.filter(NewsItem.ingested_at >= cutoff)
        if ingested_after is not None:
            query = query.filter(NewsItem.ingested_at >= ingested_after)
        if ingested_before is not None:
            query = query.filter(NewsItem.ingested_at < ingested_before)
        if args.ready_only:
            query = query.filter(NewsItem.status == "ready")
        if args.real_summaries_only:
            query = query.filter(_real_summary_filter())
        rows = (
            query.order_by(NewsItem.ingested_at.desc(), NewsItem.id.desc()).limit(args.limit).all()
        )
        if not rows:
            raise SystemExit("No news_items matched the requested export filters")

    case_id = args.case_id or _default_case_id(args=args)
    description = args.description or _default_description(
        case_id=case_id,
        row_count=len(rows),
        include_global=include_global,
        include_user_scoped=include_user_scoped,
    )
    case = NewsPipelineEvalCase(
        case_id=case_id,
        description=description,
        mode="snapshot",
        user=NewsPipelineEvalUserContext(
            user_id=source_user.id if source_user else args.user_id,
            create_if_missing=True,
            apple_id=source_user.apple_id if source_user else None,
            email=source_user.email if source_user else None,
            full_name=source_user.full_name if source_user else None,
        ),
        input_mode="news_item_records",
        news_item_records=[_serialize_news_item(row) for row in rows],
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(case.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    logger.info(
        "Exported news pipeline eval case",
        extra={
            "component": "news_pipeline_eval",
            "operation": "export_case",
            "context_data": {
                "case_id": case.case_id,
                "row_count": len(rows),
                "output": str(args.output.resolve()),
            },
        },
    )
    return 0


def _default_case_id(*, args: argparse.Namespace) -> str:
    user_part = f"user{args.user_id}" if args.user_id is not None else "global"
    platform_part = "-".join(sorted(args.platforms)) if args.platforms else "all"
    hour_part = f"{args.hours}h" if args.hours is not None else "alltime"
    return f"{user_part}-{platform_part}-{hour_part}"


def _default_description(
    *,
    case_id: str,
    row_count: int,
    include_global: bool,
    include_user_scoped: bool,
) -> str:
    scope_parts: list[str] = []
    if include_global:
        scope_parts.append("global")
    if include_user_scoped:
        scope_parts.append("user-scoped")
    scopes = " + ".join(scope_parts)
    return f"Snapshot export {case_id} with {row_count} {scopes} news_items"


if __name__ == "__main__":
    raise SystemExit(main())
