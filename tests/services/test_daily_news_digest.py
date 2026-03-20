"""Tests for daily news digest services."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

from app.models.metadata import DailyNewsRollupSummary
from app.models.schema import Content, DailyNewsDigest, ProcessingTask
from app.services.daily_news_digest import (
    MAX_DAILY_DIGEST_BULLETS,
    DailyDigestSourceItem,
    _select_rollup_prompt_sources,
    digest_requires_regeneration,
    enqueue_daily_news_digest_task,
    resolve_daily_digest_generation_target,
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


class _RetryingSummarizer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def summarize_content(self, *_args, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return DailyNewsRollupSummary(
                title="Sparse first pass",
                key_points=[
                    "AI infrastructure demand remained high.",
                    "Regulators advanced new disclosure requirements.",
                    "Payments startups raised fresh capital.",
                ],
                summary="",
            )
        return DailyNewsRollupSummary(
            title="AI, regulation, and payments shaped the day",
            key_points=[
                "AI infrastructure demand remained high across cloud providers.",
                "Regulators advanced new disclosure requirements for major platforms.",
                "Payments startups raised fresh capital to expand internationally.",
                "Defense and policy stories remained active across multiple regions.",
                "Enterprise software launches focused on agentic workflow automation.",
                "Retail and commerce funding continued despite tighter markets.",
            ],
            summary=(
                "The day combined AI infrastructure momentum, regulatory movement, and "
                "steady payments and enterprise funding activity."
            ),
        )


class _ManyBulletsSummarizer:
    def summarize_content(self, *_args, **_kwargs):
        return DailyNewsRollupSummary(
            title="A crowded news day",
            key_points=[f"Point {index}" for index in range(1, 15)],
            summary="A valid daily digest summary.",
        )


class _HighVolumeValidSummarizer:
    def summarize_content(self, *_args, **_kwargs):
        return DailyNewsRollupSummary(
            title="AI, policy, and payments led the day",
            key_points=[
                "AI infrastructure spending stayed elevated across hyperscalers.",
                "Regulators advanced disclosure and safety requirements.",
                "Payments and commerce startups continued raising capital.",
                "Defense and geopolitical developments remained active.",
                "Enterprise software launches emphasized workflow automation.",
            ],
            summary="A strong daily digest summary for a high-volume news day.",
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
    assert digest.coverage_end_at == datetime(2026, 3, 1, 0, 0, 0)
    assert isinstance(digest.key_points, list)
    assert len(digest.source_content_ids) == 2
    assert digest.llm_model == "google:gemini-3.1-flash-lite-preview"


def test_upsert_daily_news_digest_for_user_day_retries_sparse_rollup(
    db_session,
    test_user,
) -> None:
    db_session.add_all(
        [
            _build_news_content(
                url=f"https://example.com/news-{index}",
                title=f"News {index}",
                created_at=datetime(2026, 2, 28, 8, 0, 0),
                key_points=[f"Point {index}"],
            )
            for index in range(1, 11)
        ]
    )
    db_session.commit()

    summarizer = _RetryingSummarizer()
    result = upsert_daily_news_digest_for_user_day(
        db_session,
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone_name="UTC",
        summarizer=summarizer,
    )

    digest = (
        db_session.query(DailyNewsDigest)
        .filter(DailyNewsDigest.user_id == test_user.id, DailyNewsDigest.id == result.digest_id)
        .first()
    )
    assert digest is not None
    assert len(summarizer.calls) == 2
    assert summarizer.calls[1]["model_hint"] == "gemini-flash-latest"
    assert digest.title == "AI, regulation, and payments shaped the day"
    assert digest.summary.startswith("The day combined AI infrastructure momentum")
    assert len(digest.key_points) == 6


def test_upsert_daily_news_digest_caps_key_points(
    db_session,
    test_user,
) -> None:
    db_session.add_all(
        [
            _build_news_content(
                url="https://example.com/news-1",
                title="News One",
                created_at=datetime(2026, 2, 28, 9, 0, 0),
                key_points=["Point one"],
            ),
            _build_news_content(
                url="https://example.com/news-2",
                title="News Two",
                created_at=datetime(2026, 2, 28, 10, 0, 0),
                key_points=["Point two"],
            ),
        ]
    )
    db_session.commit()

    result = upsert_daily_news_digest_for_user_day(
        db_session,
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone_name="UTC",
        summarizer=_ManyBulletsSummarizer(),
    )

    digest = (
        db_session.query(DailyNewsDigest)
        .filter(DailyNewsDigest.user_id == test_user.id, DailyNewsDigest.id == result.digest_id)
        .first()
    )
    assert digest is not None
    assert len(digest.key_points) == MAX_DAILY_DIGEST_BULLETS


def test_force_regenerate_updates_existing_sparse_digest(
    db_session,
    test_user,
) -> None:
    db_session.add_all(
        [
            _build_news_content(
                url=f"https://example.com/news-{index}",
                title=f"News {index}",
                created_at=datetime(2026, 2, 28, 8, 0, 0),
                key_points=[f"Point {index}"],
            )
            for index in range(1, 11)
        ]
    )
    existing_digest = DailyNewsDigest(
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone="UTC",
        title="2026-02-28",
        summary="",
        key_points=["Only one", "Only two", "Only three"],
        source_content_ids=[],
        source_count=75,
        llm_model="google:gemini-3.1-flash-lite-preview",
        generated_at=datetime(2026, 3, 1, 3, 0, 0),
    )
    db_session.add(existing_digest)
    db_session.commit()
    db_session.refresh(existing_digest)

    assert digest_requires_regeneration(existing_digest) is True

    result = upsert_daily_news_digest_for_user_day(
        db_session,
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone_name="UTC",
        summarizer=_HighVolumeValidSummarizer(),
        force_regenerate=True,
    )

    db_session.refresh(existing_digest)
    assert result.digest_id == existing_digest.id
    assert existing_digest.title == "AI, policy, and payments led the day"
    assert existing_digest.summary == "A strong daily digest summary for a high-volume news day."


def test_upsert_daily_news_digest_for_user_day_skips_empty_checkpoint_when_requested(
    db_session,
    test_user,
) -> None:
    result = upsert_daily_news_digest_for_user_day(
        db_session,
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone_name="UTC",
        coverage_end_at=datetime(2026, 2, 28, 0, 0, 0),
        skip_if_empty=True,
    )

    assert result.skipped is True
    assert result.digest_id is None
    assert (
        db_session.query(DailyNewsDigest)
        .filter(
            DailyNewsDigest.user_id == test_user.id,
            DailyNewsDigest.local_date == datetime(2026, 2, 28).date(),
        )
        .first()
        is None
    )


def test_upsert_daily_news_digest_for_user_day_updates_existing_digest_and_resets_read_at(
    db_session,
    test_user,
) -> None:
    first_story = _build_news_content(
        url="https://example.com/news-1",
        title="News One",
        created_at=datetime(2026, 2, 28, 1, 0, 0),
        key_points=["Point one"],
    )
    second_story = _build_news_content(
        url="https://example.com/news-2",
        title="News Two",
        created_at=datetime(2026, 2, 28, 5, 0, 0),
        key_points=["Point two"],
    )
    db_session.add_all([first_story, second_story])
    db_session.commit()

    existing_digest = DailyNewsDigest(
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone="UTC",
        title="Earlier digest",
        summary="Earlier summary",
        key_points=["Point one"],
        source_content_ids=[first_story.id],
        source_count=1,
        llm_model="google:gemini-3.1-flash-lite-preview",
        generated_at=datetime(2026, 2, 28, 3, 0, 0),
        coverage_end_at=datetime(2026, 2, 28, 3, 0, 0),
        read_at=datetime(2026, 2, 28, 3, 30, 0),
    )
    db_session.add(existing_digest)
    db_session.commit()

    result = upsert_daily_news_digest_for_user_day(
        db_session,
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone_name="UTC",
        coverage_end_at=datetime(2026, 2, 28, 6, 0, 0),
        summarizer=_StubSummarizer(),
    )

    db_session.refresh(existing_digest)
    assert result.digest_id == existing_digest.id
    assert existing_digest.source_count == 2
    assert existing_digest.coverage_end_at == datetime(2026, 2, 28, 6, 0, 0)
    assert existing_digest.read_at is None


def test_upsert_daily_news_digest_for_user_day_preserves_read_at_when_sources_do_not_change(
    db_session,
    test_user,
) -> None:
    story = _build_news_content(
        url="https://example.com/news-1",
        title="News One",
        created_at=datetime(2026, 2, 28, 1, 0, 0),
        key_points=["Point one"],
    )
    db_session.add(story)
    db_session.commit()

    existing_read_at = datetime(2026, 2, 28, 3, 30, 0)
    existing_digest = DailyNewsDigest(
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone="UTC",
        title="Earlier digest",
        summary="Earlier summary",
        key_points=["Point one"],
        source_content_ids=[story.id],
        source_count=1,
        llm_model="google:gemini-3.1-flash-lite-preview",
        generated_at=datetime(2026, 2, 28, 3, 0, 0),
        coverage_end_at=datetime(2026, 2, 28, 3, 0, 0),
        read_at=existing_read_at,
    )
    db_session.add(existing_digest)
    db_session.commit()

    result = upsert_daily_news_digest_for_user_day(
        db_session,
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone_name="UTC",
        coverage_end_at=datetime(2026, 2, 28, 6, 0, 0),
        summarizer=_StubSummarizer(),
    )

    db_session.refresh(existing_digest)
    assert result.digest_id == existing_digest.id
    assert existing_digest.coverage_end_at == datetime(2026, 2, 28, 6, 0, 0)
    assert existing_digest.read_at == existing_read_at


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
        llm_model="google:gemini-3.1-flash-lite-preview",
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
        llm_model="google:gemini-3.1-flash-lite-preview",
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


def test_enqueue_daily_news_digest_task_dedupes_per_checkpoint(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    existing_task = ProcessingTask(
        task_type="generate_daily_news_digest",
        status="pending",
        payload={
            "user_id": test_user.id,
            "local_date": "2026-02-28",
            "coverage_end_at": "2026-02-28T03:00:00",
        },
    )
    db_session.add(existing_task)
    db_session.commit()
    db_session.refresh(existing_task)

    mock_queue = Mock()
    mock_queue.enqueue.return_value = 931
    monkeypatch.setattr("app.services.daily_news_digest.get_queue_service", lambda: mock_queue)

    same_checkpoint_task_id = enqueue_daily_news_digest_task(
        db_session,
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone_name="UTC",
        coverage_end_at=datetime(2026, 2, 28, 3, 0, 0),
    )
    later_checkpoint_task_id = enqueue_daily_news_digest_task(
        db_session,
        user_id=test_user.id,
        local_date=datetime(2026, 2, 28).date(),
        timezone_name="UTC",
        coverage_end_at=datetime(2026, 2, 28, 6, 0, 0),
    )

    assert same_checkpoint_task_id == existing_task.id
    assert later_checkpoint_task_id == 931
    mock_queue.enqueue.assert_called_once()


def test_resolve_daily_digest_generation_target_matches_recent_window() -> None:
    target = resolve_daily_digest_generation_target(
        "UTC",
        now_utc=datetime.fromisoformat("2026-03-09T06:00:00+00:00"),
        interval_hours=6,
        lookback_hours=6,
    )

    assert target is not None
    assert target.local_date.isoformat() == "2026-03-09"
    assert target.coverage_end_at == datetime(2026, 3, 9, 6, 0, 0)


def test_resolve_daily_digest_generation_target_skips_old_schedule_outside_window() -> None:
    target = resolve_daily_digest_generation_target(
        "UTC",
        now_utc=datetime.fromisoformat("2026-03-09T08:59:00+00:00"),
        interval_hours=6,
        lookback_hours=2,
    )

    assert target is None


def test_resolve_daily_digest_generation_target_handles_non_utc_timezone() -> None:
    target = resolve_daily_digest_generation_target(
        "America/Los_Angeles",
        now_utc=datetime.fromisoformat("2026-03-09T12:00:00+00:00"),
        interval_hours=6,
        lookback_hours=6,
    )

    assert target is not None
    assert target.local_date.isoformat() == "2026-03-09"
    assert target.coverage_end_at == datetime(2026, 3, 9, 7, 0, 0)


def test_resolve_daily_digest_generation_target_handles_half_hour_timezone() -> None:
    target = resolve_daily_digest_generation_target(
        "Asia/Kolkata",
        now_utc=datetime.fromisoformat("2026-03-09T00:00:00+00:00"),
        interval_hours=3,
        lookback_hours=6,
    )

    assert target is not None
    assert target.local_date.isoformat() == "2026-03-09"
    assert target.coverage_end_at == datetime(2026, 3, 8, 21, 30, 0)
