"""Tests for daily news digest services."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

from app.models.metadata import DailyNewsRollupSummary
from app.models.schema import Content, DailyNewsDigest
from app.services.daily_news_digest import (
    DailyDigestSourceItem,
    _select_rollup_prompt_sources,
    enqueue_daily_news_digest_task,
    upsert_daily_news_digest_for_user_day,
)


class _StubSummarizer:
    def summarize_content(self, *_args, **_kwargs):
        return DailyNewsRollupSummary(
            title="Market and AI moved fast",
            key_points=[
                "Chip demand remained elevated across cloud providers.",
                "Regulators advanced new disclosure requirements.",
            ],
            summary=(
                "A tight day with continued AI infrastructure demand "
                "and incremental policy movement."
            ),
        )


def _build_news_content(
    *,
    url: str,
    title: str,
    created_at: datetime,
    key_points: list[str],
) -> Content:
    return Content(
        url=url,
        title=title,
        content_type="news",
        status="completed",
        classification="to_read",
        created_at=created_at,
        content_metadata={
            "summary": {
                "title": title,
                "key_points": key_points,
                "summary": key_points[0] if key_points else "",
            }
        },
    )


def test_upsert_daily_news_digest_for_user_day_builds_row(db_session, test_user) -> None:
    db_session.add_all(
        [
            _build_news_content(
                url="https://example.com/news-1",
                title="News One",
                created_at=datetime(2026, 2, 28, 9, 0, 0),
                key_points=["Point one", "Point two"],
            ),
            _build_news_content(
                url="https://example.com/news-2",
                title="News Two",
                created_at=datetime(2026, 2, 28, 15, 30, 0),
                key_points=["Point three"],
            ),
            _build_news_content(
                url="https://example.com/news-outside-day",
                title="News Outside",
                created_at=datetime(2026, 2, 27, 22, 0, 0),
                key_points=["Older point"],
            ),
        ]
    )
    db_session.commit()

    result = upsert_daily_news_digest_for_user_day(
        db_session,
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone_name="UTC",
        summarizer=_StubSummarizer(),
    )

    digest = (
        db_session.query(DailyNewsDigest)
        .filter(DailyNewsDigest.user_id == test_user.id, DailyNewsDigest.id == result.digest_id)
        .first()
    )
    assert digest is not None
    assert digest.local_date.isoformat() == "2026-02-28"
    assert digest.source_count == 2
    assert digest.read_at is None
    assert digest.title == "Market and AI moved fast"
    assert (
        digest.summary
        == "A tight day with continued AI infrastructure demand and incremental policy movement."
    )
    assert isinstance(digest.key_points, list)
    assert len(digest.source_content_ids) == 2


def test_select_rollup_prompt_sources_trims_only_when_budget_requires() -> None:
    sources = [
        DailyDigestSourceItem(
            content_id=index,
            title=f"Story {index}",
            key_points=[
                "A" * 320,
                "B" * 320,
            ],
        )
        for index in range(1, 6)
    ]

    selected = _select_rollup_prompt_sources(
        local_date=datetime(2026, 2, 28).date(),
        sources=sources,
        token_budget=260,
    )

    assert len(selected) < len(sources)
    assert len(selected) >= 1
    assert selected == sources[: len(selected)]


def test_select_rollup_prompt_sources_keeps_all_sources_when_under_budget() -> None:
    sources = [
        DailyDigestSourceItem(
            content_id=index,
            title=f"Story {index}",
            key_points=["Short signal"],
        )
        for index in range(1, 4)
    ]

    selected = _select_rollup_prompt_sources(
        local_date=datetime(2026, 2, 28).date(),
        sources=sources,
        token_budget=2_000,
    )

    assert selected == sources


def test_enqueue_daily_news_digest_task_force_regenerate_ignores_existing_digest(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    existing_digest = DailyNewsDigest(
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone="UTC",
        title="Existing",
        summary="Existing summary",
        key_points=[],
        source_content_ids=[],
        source_count=0,
        llm_model="google-gla:gemini-flash-latest",
        generated_at=datetime(2026, 3, 1, 3, 0, 0),
    )
    db_session.add(existing_digest)
    db_session.commit()

    mock_queue = Mock()
    mock_queue.enqueue.return_value = 915
    monkeypatch.setattr("app.services.daily_news_digest.get_queue_service", lambda: mock_queue)

    task_id = enqueue_daily_news_digest_task(
        db_session,
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone_name="UTC",
        trigger="backfill",
        force_regenerate=True,
    )

    assert task_id == 915
    mock_queue.enqueue.assert_called_once()


def test_enqueue_daily_news_digest_task_skips_existing_digest_without_force(
    db_session,
    test_user,
) -> None:
    existing_digest = DailyNewsDigest(
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone="UTC",
        title="Existing",
        summary="Existing summary",
        key_points=[],
        source_content_ids=[],
        source_count=0,
        llm_model="google-gla:gemini-flash-latest",
        generated_at=datetime(2026, 3, 1, 3, 0, 0),
    )
    db_session.add(existing_digest)
    db_session.commit()

    task_id = enqueue_daily_news_digest_task(
        db_session,
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone_name="UTC",
    )

    assert task_id is None
