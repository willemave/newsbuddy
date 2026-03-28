"""Backfill daily news digests for specific user IDs and date ranges."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import get_db
from app.core.logging import get_logger, setup_logging
from app.core.settings import get_settings
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content
from app.models.user import User
from app.pipeline.handlers.fetch_discussion import FetchDiscussionHandler
from app.pipeline.handlers.process_content import ProcessContentHandler
from app.pipeline.handlers.summarize import SummarizeHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.scraping.runner import ScraperRunner
from app.services.content_submission import normalize_url
from app.services.daily_news_digest import (
    enqueue_daily_news_digest_task,
    get_local_digest_window_utc_bounds,
    normalize_timezone,
    upsert_daily_news_digest_for_user_day,
)
from app.services.llm_summarization import get_content_summarizer
from app.services.queue import TaskType, get_queue_service
from app.services.scraper_configs import ensure_inbox_status

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill daily news digests")
    parser.add_argument(
        "--user-id",
        type=int,
        action="append",
        dest="user_ids",
        help="Target user ID. Can be repeated.",
    )
    parser.add_argument(
        "--email",
        type=str,
        action="append",
        dest="emails",
        help="Target user email. Can be repeated.",
    )
    parser.add_argument(
        "--from-date",
        type=str,
        default=None,
        help="Start local date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--to-date",
        type=str,
        default=None,
        help="End local date inclusive (YYYY-MM-DD). Defaults to --from-date.",
    )
    parser.add_argument(
        "--recent-days",
        type=int,
        default=None,
        help="Backfill the last N completed local days per user instead of explicit dates.",
    )
    parser.add_argument(
        "--now-utc",
        type=str,
        default=None,
        help="Override current UTC time (ISO8601) for recent-day calculations.",
    )
    parser.add_argument(
        "--inline",
        action="store_true",
        help="Generate digests immediately instead of enqueuing tasks.",
    )
    parser.add_argument(
        "--force-regenerate",
        action="store_true",
        help="Regenerate even if digest row already exists.",
    )
    parser.add_argument(
        "--trigger",
        type=str,
        default="backfill",
        help="Trigger label stored in task payload when enqueuing.",
    )
    parser.add_argument(
        "--refresh-twitter",
        action="store_true",
        help=(
            "Scrape the configured Twitter/X lists first, then synchronously process newly "
            "ingested Twitter news items before generating digests."
        ),
    )
    parser.add_argument(
        "--twitter-article-count",
        type=int,
        default=0,
        help=(
            "Create up to N article rows per user/day from the latest completed Twitter-linked "
            "news sources in that local digest window."
        ),
    )
    return parser.parse_args()


def _parse_date(raw_value: str, *, flag: str) -> date:
    try:
        return date.fromisoformat(raw_value)
    except ValueError as exc:
        raise ValueError(f"Invalid {flag}: {raw_value}") from exc


def _parse_now_utc(raw_value: str | None) -> datetime:
    if not raw_value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(raw_value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _resolve_target_dates(
    *,
    timezone_name: str,
    from_date: date | None,
    to_date: date | None,
    recent_days: int | None,
    now_utc: datetime,
) -> tuple[date, date]:
    if recent_days is not None:
        if recent_days <= 0:
            raise ValueError("--recent-days must be >= 1")
        local_today = now_utc.astimezone(ZoneInfo(timezone_name)).date()
        return local_today - timedelta(days=recent_days), local_today - timedelta(days=1)

    if from_date is None:
        raise ValueError("Provide --from-date/--to-date or use --recent-days")

    end_date = to_date or from_date
    if end_date < from_date:
        raise ValueError("--to-date must be >= --from-date")
    return from_date, end_date


def _iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _build_task_context(*, worker_id: str) -> TaskContext:
    return TaskContext(
        queue_service=get_queue_service(),
        settings=get_settings(),
        llm_service=get_content_summarizer(),
        worker_id=worker_id,
    )


def _run_process_handler(
    *,
    handler: ProcessContentHandler,
    content_id: int,
    context: TaskContext,
) -> None:
    result = handler.handle(
        TaskEnvelope(
            id=content_id,
            task_type=TaskType.PROCESS_CONTENT,
            content_id=content_id,
        ),
        context,
    )
    if not result.success:
        logger.warning(
            "Manual PROCESS_CONTENT failed for content_id=%s error=%s retryable=%s",
            content_id,
            result.error_message,
            result.retryable,
        )


def _run_summarize_handler(
    *,
    handler: SummarizeHandler,
    content_id: int,
    context: TaskContext,
) -> None:
    result = handler.handle(
        TaskEnvelope(
            id=100_000 + content_id,
            task_type=TaskType.SUMMARIZE,
            content_id=content_id,
        ),
        context,
    )
    if not result.success:
        logger.warning(
            "Manual SUMMARIZE failed for content_id=%s error=%s retryable=%s",
            content_id,
            result.error_message,
            result.retryable,
        )


def _run_fetch_discussion_handler(
    *,
    handler: FetchDiscussionHandler,
    content_id: int,
    context: TaskContext,
) -> None:
    result = handler.handle(
        TaskEnvelope(
            id=200_000 + content_id,
            task_type=TaskType.FETCH_DISCUSSION,
            content_id=content_id,
        ),
        context,
    )
    if not result.success:
        logger.warning(
            "Manual FETCH_DISCUSSION failed for content_id=%s error=%s retryable=%s",
            content_id,
            result.error_message,
            result.retryable,
        )


def _refresh_twitter_sources(*, context: TaskContext) -> list[int]:
    with get_db() as db:
        max_content_id_before = db.query(func.max(Content.id)).scalar() or 0

    scraper_runner = ScraperRunner()
    stats = scraper_runner.run_scraper_with_stats("Twitter")
    if stats is None:
        logger.warning("Twitter scraper did not return stats; skipping synchronous processing")
        return []

    with get_db() as db:
        new_ids = [
            content_id
            for (content_id,) in (
                db.query(Content.id)
                .filter(Content.id > int(max_content_id_before))
                .filter(Content.platform == "twitter")
                .filter(Content.content_type == ContentType.NEWS.value)
                .order_by(Content.id.asc())
                .all()
            )
        ]

    logger.info(
        "Twitter refresh completed scraped=%s saved=%s new_content_ids=%s",
        stats.scraped,
        stats.saved,
        new_ids,
    )

    if not new_ids:
        return []

    process_handler = ProcessContentHandler()
    summarize_handler = SummarizeHandler()
    fetch_discussion_handler = FetchDiscussionHandler()

    for content_id in new_ids:
        _run_process_handler(handler=process_handler, content_id=content_id, context=context)
        _run_summarize_handler(handler=summarize_handler, content_id=content_id, context=context)
        _run_fetch_discussion_handler(
            handler=fetch_discussion_handler,
            content_id=content_id,
            context=context,
        )

    return new_ids


def _resolve_twitter_article_candidates(
    db: Session,
    *,
    local_date: date,
    timezone_name: str,
    limit: int,
) -> list[dict[str, str]]:
    if limit <= 0:
        return []

    start_utc, end_utc = get_local_digest_window_utc_bounds(local_date, timezone_name)
    candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    rows = (
        db.query(Content)
        .filter(Content.platform == "twitter")
        .filter(Content.content_type == ContentType.NEWS.value)
        .filter(Content.status == ContentStatus.COMPLETED.value)
        .filter(Content.created_at >= start_utc, Content.created_at < end_utc)
        .order_by(Content.created_at.desc(), Content.id.desc())
        .all()
    )

    for content in rows:
        metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
        article = metadata.get("article") if isinstance(metadata.get("article"), dict) else {}
        raw_url = article.get("url") or content.url
        try:
            article_url = normalize_url(str(raw_url))
        except Exception:  # noqa: BLE001
            continue
        if article_url in seen_urls:
            continue
        seen_urls.add(article_url)
        candidates.append(
            {
                "article_url": article_url,
                "title": str(article.get("title") or content.title or "").strip(),
                "source_domain": str(article.get("source_domain") or content.source or "").strip(),
                "source_url": str(content.source_url or content.url or "").strip(),
                "discussion_url": str(metadata.get("discussion_url") or "").strip(),
            }
        )
        if len(candidates) >= limit:
            break

    return candidates


def _create_or_load_article_from_twitter_source(
    db: Session,
    *,
    user_id: int,
    candidate: dict[str, str],
) -> int:
    article_url = candidate["article_url"]
    existing = (
        db.query(Content)
        .filter(Content.url == article_url, Content.content_type == ContentType.ARTICLE.value)
        .first()
    )
    if existing is not None:
        ensure_inbox_status(
            db,
            user_id=user_id,
            content_id=existing.id,
            content_type=existing.content_type,
        )
        db.commit()
        return int(existing.id)

    metadata: dict[str, Any] = {
        "source": candidate["source_domain"] or "twitter",
        "submitted_by_user_id": user_id,
        "submitted_via": "backfill_daily_news_digests_twitter_article",
        "platform_hint": "twitter",
    }
    if candidate["discussion_url"]:
        metadata["discussion_url"] = candidate["discussion_url"]

    article = Content(
        url=article_url,
        source_url=candidate["source_url"] or article_url,
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.PENDING.value,
        title=candidate["title"] or None,
        source=candidate["source_domain"] or None,
        platform="twitter",
        content_metadata=metadata,
    )
    db.add(article)
    db.commit()
    db.refresh(article)

    ensure_inbox_status(
        db,
        user_id=user_id,
        content_id=article.id,
        content_type=article.content_type,
    )
    db.commit()
    return int(article.id)


def _materialize_twitter_articles_for_user_day(
    db: Session,
    *,
    user_id: int,
    local_date: date,
    timezone_name: str,
    article_count: int,
    context: TaskContext,
) -> list[int]:
    candidates = _resolve_twitter_article_candidates(
        db,
        local_date=local_date,
        timezone_name=timezone_name,
        limit=article_count,
    )
    if not candidates:
        return []

    process_handler = ProcessContentHandler()
    summarize_handler = SummarizeHandler()
    article_ids: list[int] = []

    for candidate in candidates:
        article_id = _create_or_load_article_from_twitter_source(
            db,
            user_id=user_id,
            candidate=candidate,
        )
        article_ids.append(article_id)
        _run_process_handler(handler=process_handler, content_id=article_id, context=context)
        _run_summarize_handler(handler=summarize_handler, content_id=article_id, context=context)

    logger.info(
        "Materialized Twitter article rows user_id=%s local_date=%s article_ids=%s",
        user_id,
        local_date.isoformat(),
        article_ids,
    )
    return article_ids


def main() -> None:
    setup_logging()
    args = _parse_args()
    now_utc = _parse_now_utc(args.now_utc)

    if args.recent_days is not None and (args.from_date or args.to_date):
        raise ValueError("--recent-days cannot be combined with --from-date/--to-date")
    if args.twitter_article_count < 0:
        raise ValueError("--twitter-article-count must be >= 0")

    start_date = _parse_date(args.from_date, flag="--from-date") if args.from_date else None
    end_date = _parse_date(args.to_date, flag="--to-date") if args.to_date else None

    requested_user_ids = sorted({user_id for user_id in (args.user_ids or []) if user_id > 0})
    requested_emails = sorted(
        {
            email.strip().lower()
            for email in (args.emails or [])
            if isinstance(email, str) and email.strip()
        }
    )
    if not requested_user_ids and not requested_emails:
        raise ValueError("At least one --user-id or --email is required")

    logger.info(
        (
            "Starting daily digest backfill user_ids=%s emails=%s "
            "from=%s to=%s recent_days=%s inline=%s force_regenerate=%s "
            "refresh_twitter=%s twitter_article_count=%s now_utc=%s"
        ),
        requested_user_ids,
        requested_emails,
        start_date.isoformat() if start_date else None,
        end_date.isoformat() if end_date else None,
        args.recent_days,
        args.inline,
        args.force_regenerate,
        args.refresh_twitter,
        args.twitter_article_count,
        now_utc.isoformat(),
    )

    processed_users = 0
    processed_days = 0
    enqueued_or_generated = 0
    skipped_missing_users = 0
    task_context = _build_task_context(worker_id="daily-digest-backfill")

    if args.refresh_twitter:
        _refresh_twitter_sources(context=task_context)

    with get_db() as db:
        user_filters = []
        if requested_user_ids:
            user_filters.append(User.id.in_(requested_user_ids))
        if requested_emails:
            user_filters.append(func.lower(User.email).in_(requested_emails))

        users = db.query(User).filter(or_(*user_filters)).order_by(User.id.asc()).all()
        users_by_id = {user.id: user for user in users}

        if requested_user_ids:
            for user_id in requested_user_ids:
                if user_id in users_by_id:
                    continue
                skipped_missing_users += 1
                logger.warning("Skipping unknown user_id=%s", user_id)

        found_emails = {str(user.email).strip().lower() for user in users if user.email}
        for email in requested_emails:
            if email in found_emails:
                continue
            skipped_missing_users += 1
            logger.warning("Skipping unknown email=%s", email)

        for user in users:
            if user.id not in users_by_id:
                continue

            processed_users += 1
            timezone_name = normalize_timezone(getattr(user, "news_digest_timezone", None))
            user_start_date, user_end_date = _resolve_target_dates(
                timezone_name=timezone_name,
                from_date=start_date,
                to_date=end_date,
                recent_days=args.recent_days,
                now_utc=now_utc,
            )

            for target_date in _iter_dates(user_start_date, user_end_date):
                processed_days += 1
                if args.twitter_article_count > 0:
                    _materialize_twitter_articles_for_user_day(
                        db,
                        user_id=user.id,
                        local_date=target_date,
                        timezone_name=timezone_name,
                        article_count=args.twitter_article_count,
                        context=task_context,
                    )
                if args.inline:
                    result = upsert_daily_news_digest_for_user_day(
                        db,
                        user_id=user.id,
                        local_date=target_date,
                        timezone_name=timezone_name,
                        force_regenerate=args.force_regenerate,
                    )
                    enqueued_or_generated += 1
                    logger.info(
                        (
                            "Generated digest inline user=%s email=%s local_date=%s "
                            "digest_id=%s sources=%s created=%s"
                        ),
                        user.id,
                        user.email,
                        target_date.isoformat(),
                        result.digest_id,
                        result.source_count,
                        result.created,
                    )
                    continue

                task_id = enqueue_daily_news_digest_task(
                    db,
                    user_id=user.id,
                    local_date=target_date,
                    timezone_name=timezone_name,
                    trigger=args.trigger,
                    force_regenerate=args.force_regenerate,
                )
                if task_id is not None:
                    enqueued_or_generated += 1
                    logger.info(
                        "Enqueued digest task user=%s email=%s local_date=%s task_id=%s",
                        user.id,
                        user.email,
                        target_date.isoformat(),
                        task_id,
                    )

    logger.info(
        (
            "Daily digest backfill summary users_processed=%s days_processed=%s "
            "enqueued_or_generated=%s missing_users=%s"
        ),
        processed_users,
        processed_days,
        enqueued_or_generated,
        skipped_missing_users,
    )


if __name__ == "__main__":
    main()
