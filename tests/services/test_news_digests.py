"""Tests for news-native digest grouping and persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from app.models.news_digest_models import (
    NewsDigestBatchBulletDraft,
    NewsDigestBulletDraft,
    NewsDigestHeaderDraft,
)
from app.models.schema import NewsDigest, NewsDigestBullet, NewsItem, NewsItemDigestCoverage
from app.models.user import User
from app.services import news_digests


def _build_news_item(
    item_id: int,
    *,
    story_url: str,
    item_url: str,
    title: str,
    source_label: str,
) -> NewsItem:
    return NewsItem(
        id=item_id,
        ingest_key=f"item-{item_id}",
        visibility_scope="global",
        owner_user_id=None,
        platform="hackernews",
        source_type="hackernews",
        source_label=source_label,
        source_external_id=str(item_id),
        canonical_item_url=item_url,
        canonical_story_url=story_url,
        article_url=story_url,
        article_title=title,
        article_domain="example.com",
        discussion_url=item_url,
        summary_title=title,
        summary_key_points=["Shared point"],
        summary_text=f"Summary for {title}",
        raw_metadata={},
        status="ready",
        ingested_at=datetime.now(UTC).replace(tzinfo=None),
    )


def test_cluster_news_items_exact_dedupes_shared_story(monkeypatch) -> None:
    monkeypatch.setattr(
        news_digests,
        "encode_news_texts",
        lambda texts: np.eye(len(texts), dtype=np.float32),
    )
    first = _build_news_item(
        1,
        story_url="https://example.com/story",
        item_url="https://news.ycombinator.com/item?id=1",
        title="Same story from HN",
        source_label="Hacker News",
    )
    second = _build_news_item(
        2,
        story_url="https://example.com/story",
        item_url="https://www.reddit.com/r/test/comments/2",
        title="Same story from Reddit",
        source_label="Reddit",
    )
    third = _build_news_item(
        3,
        story_url="https://example.com/other",
        item_url="https://news.ycombinator.com/item?id=3",
        title="Different story",
        source_label="Hacker News",
    )

    clusters = news_digests.cluster_news_items([first, second, third])

    assert len(clusters) == 2
    assert sorted(item.id for item in clusters[0].items) == [1, 2]


def test_matching_text_prefers_one_title_and_excludes_source_label() -> None:
    item = _build_news_item(
        9,
        story_url="https://example.com/story-z",
        item_url="https://news.ycombinator.com/item?id=9",
        title="Article title should be ignored when summary title exists",
        source_label="Hacker News",
    )
    item.summary_title = "Summary title wins"
    item.summary_key_points = ["First point", "Second point"]
    item.summary_text = "Body summary text."

    matching_text = news_digests._matching_text(item)

    assert matching_text.splitlines()[0] == "Summary title wins"
    assert "Article title should be ignored" not in matching_text
    assert "Hacker News" not in matching_text
    assert "example.com" in matching_text
    assert "First point" in matching_text
    assert "Body summary text." in matching_text


def test_generate_news_digest_for_user_persists_bullets_and_coverage(
    db_session,
    monkeypatch,
) -> None:
    user = User(
        apple_id="digest-user",
        email="digest@example.com",
        full_name="Digest User",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    first = _build_news_item(
        11,
        story_url="https://example.com/story-a",
        item_url="https://news.ycombinator.com/item?id=11",
        title="Story A",
        source_label="Hacker News",
    )
    second = _build_news_item(
        12,
        story_url="https://example.com/story-b",
        item_url="https://news.ycombinator.com/item?id=12",
        title="Story B",
        source_label="Hacker News",
    )
    db_session.add_all([first, second])
    db_session.commit()

    monkeypatch.setattr(
        news_digests,
        "cluster_news_items",
        lambda items: [
            news_digests.NewsDigestCluster(items=[items[0]]),
            news_digests.NewsDigestCluster(items=[items[1]]),
        ],
    )
    monkeypatch.setattr(
        news_digests,
        "_generate_curated_cluster_bullets",
        lambda **kwargs: (
            [
                news_digests.NewsDigestCuratedBulletDraft(
                    cluster=cluster,
                    draft=NewsDigestBulletDraft(
                        topic=cluster.items[0].summary_title or "Topic",
                        details="This cluster contains enough detail for the digest bullet.",
                        news_item_ids=[cluster.items[0].id],
                    ),
                )
                for cluster in kwargs["clusters"]
            ],
            True,
        ),
    )
    monkeypatch.setattr(
        news_digests,
        "_generate_header_draft",
        lambda bullets: NewsDigestHeaderDraft(
            title="Morning digest",
            summary="Two distinct stories landed in this run.",
        ),
    )

    result = news_digests.generate_news_digest_for_user(
        db_session,
        user_id=user.id,
        trigger_reason="manual_test",
        force=True,
    )
    db_session.commit()

    assert result.skipped is False
    assert db_session.query(NewsDigest).count() == 1
    assert db_session.query(NewsDigestBullet).count() == 2
    assert db_session.query(NewsItemDigestCoverage).count() == 2


def test_generate_curated_cluster_bullets_uses_user_prompt_and_sanitizes_ids(monkeypatch) -> None:
    user = User(
        apple_id="digest-preference-user",
        email="digest-preference@example.com",
        news_digest_preference_prompt="Prefer semiconductor supply chain and AI infra.",
    )
    cluster = news_digests.NewsDigestCluster(
        items=[
            _build_news_item(
                21,
                story_url="https://example.com/story-c",
                item_url="https://news.ycombinator.com/item?id=21",
                title="Story C",
                source_label="Hacker News",
            )
        ]
    )
    captured: dict[str, str] = {}

    def fake_get_basic_agent(_model_spec, output_cls, system_prompt):
        captured["system_prompt"] = system_prompt

        class _Agent:
            def run_sync(self, prompt, model_settings=None):  # noqa: ANN001
                captured["prompt"] = prompt
                return SimpleNamespace(
                    output=output_cls(
                        bullets=[
                            NewsDigestBatchBulletDraft(
                                cluster_rank=1,
                                topic="Semiconductor capacity stays tight",
                                details="Capacity and packaging constraints remain the main story.",
                                news_item_ids=[9999, 21],
                            )
                        ]
                    )
                )

        return _Agent()

    monkeypatch.setattr(news_digests, "get_basic_agent", fake_get_basic_agent)

    curated, used_batch = news_digests._generate_curated_cluster_bullets(
        user=user,
        clusters=[cluster],
    )

    assert used_batch is True
    assert "Prefer semiconductor supply chain and AI infra." in captured["system_prompt"]
    assert "\"cluster_rank\": 1" in captured["prompt"]
    assert curated[0].draft.news_item_ids == [21]


def test_generate_curated_cluster_bullets_raises_when_batch_generation_fails(monkeypatch) -> None:
    user = User(apple_id="fallback-user", email="fallback@example.com")
    cluster = news_digests.NewsDigestCluster(
        items=[
            _build_news_item(
                31,
                story_url="https://example.com/story-d",
                item_url="https://news.ycombinator.com/item?id=31",
                title="Story D",
                source_label="Techmeme",
            )
        ]
    )

    monkeypatch.setattr(
        news_digests,
        "get_basic_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("batch failed")),
    )
    monkeypatch.setattr(
        news_digests,
        "_generate_bullet_draft",
        lambda _cluster: NewsDigestBulletDraft(
            topic="Fallback topic",
            details="Fallback details remain grounded in the representative item.",
            news_item_ids=[31],
        ),
    )

    with pytest.raises(RuntimeError, match="News digest batch curation failed"):
        news_digests._generate_curated_cluster_bullets(
            user=user,
            clusters=[cluster],
        )


def test_generate_news_digest_for_user_only_covers_curated_clusters(
    db_session,
    monkeypatch,
) -> None:
    user = User(
        apple_id="curated-coverage-user",
        email="curated-coverage@example.com",
        full_name="Curated Coverage User",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    first = _build_news_item(
        41,
        story_url="https://example.com/story-e",
        item_url="https://news.ycombinator.com/item?id=41",
        title="Story E",
        source_label="Hacker News",
    )
    second = _build_news_item(
        42,
        story_url="https://example.com/story-f",
        item_url="https://news.ycombinator.com/item?id=42",
        title="Story F",
        source_label="Techmeme",
    )
    db_session.add_all([first, second])
    db_session.commit()

    clusters = [
        news_digests.NewsDigestCluster(items=[first]),
        news_digests.NewsDigestCluster(items=[second]),
    ]
    monkeypatch.setattr(news_digests, "cluster_news_items", lambda items: clusters)
    monkeypatch.setattr(
        news_digests,
        "_generate_curated_cluster_bullets",
        lambda **kwargs: (
            [
                news_digests.NewsDigestCuratedBulletDraft(
                    cluster=kwargs["clusters"][0],
                    draft=NewsDigestBulletDraft(
                        topic="Curated story",
                        details="Only the first cluster should be persisted and covered.",
                        news_item_ids=[41],
                    ),
                )
            ],
            True,
        ),
    )
    monkeypatch.setattr(
        news_digests,
        "_generate_header_draft",
        lambda bullets: NewsDigestHeaderDraft(
            title="Curated digest",
            summary="Only the selected cluster made it into this run.",
        ),
    )

    result = news_digests.generate_news_digest_for_user(
        db_session,
        user_id=user.id,
        trigger_reason="manual_test",
        force=True,
    )
    db_session.commit()

    coverage_rows = db_session.query(NewsItemDigestCoverage).all()

    assert result.group_count == 1
    assert db_session.query(NewsDigestBullet).count() == 1
    assert [row.news_item_id for row in coverage_rows] == [41]


def test_get_news_digest_trigger_decision_flushes_day_rollover(db_session, monkeypatch) -> None:
    user = User(
        apple_id="rollover-user",
        email="rollover@example.com",
        full_name="Rollover User",
        is_active=True,
        news_digest_timezone="US/Pacific",
    )
    db_session.add(user)
    db_session.flush()

    stale_item = NewsItem(
        ingest_key="stale-item",
        visibility_scope="global",
        platform="techmeme",
        source_type="techmeme",
        source_label="Techmeme",
        source_external_id="stale",
        canonical_item_url="https://www.techmeme.com/240101/p1",
        canonical_story_url="https://example.com/stale",
        article_url="https://example.com/stale",
        article_title="Older story",
        article_domain="example.com",
        discussion_url="https://www.techmeme.com/240101/p1",
        summary_title="Older story",
        summary_key_points=["Older point"],
        summary_text="Older summary",
        raw_metadata={},
        status="ready",
        ingested_at=(datetime(2026, 3, 27, 23, 0, tzinfo=UTC) - timedelta(hours=24)).replace(
            tzinfo=None
        ),
    )
    db_session.add(stale_item)
    db_session.commit()

    monkeypatch.setattr(
        news_digests,
        "cluster_news_items",
        lambda items: [news_digests.NewsDigestCluster(items=items)],
    )

    decision = news_digests.get_news_digest_trigger_decision(
        db_session,
        user=user,
        now_utc=datetime(2026, 3, 28, 10, 0, tzinfo=UTC),
    )

    assert decision.should_generate is True
    assert decision.trigger_reason == "day_rollover_flush"


def test_get_news_digest_trigger_decision_day_rollover_only_flushes_once_per_day(
    db_session,
    monkeypatch,
) -> None:
    user = User(
        apple_id="rollover-once-user",
        email="rollover-once@example.com",
        full_name="Rollover Once User",
        is_active=True,
        news_digest_timezone="America/Los_Angeles",
    )
    db_session.add(user)
    db_session.flush()

    stale_item = NewsItem(
        ingest_key="rollover-once-item",
        visibility_scope="global",
        platform="techmeme",
        source_type="techmeme",
        source_label="Techmeme",
        source_external_id="rollover-once",
        canonical_item_url="https://www.techmeme.com/240101/p2",
        canonical_story_url="https://example.com/rollover-once",
        article_url="https://example.com/rollover-once",
        article_title="Backlog story",
        article_domain="example.com",
        discussion_url="https://www.techmeme.com/240101/p2",
        summary_title="Backlog story",
        summary_key_points=["Backlog point"],
        summary_text="Backlog summary",
        raw_metadata={},
        status="ready",
        ingested_at=datetime(2026, 3, 30, 6, 30),
    )
    db_session.add(stale_item)
    db_session.flush()
    db_session.add(
        NewsDigest(
            user_id=user.id,
            timezone="America/Los_Angeles",
            title="Earlier digest",
            summary="Already flushed today.",
            source_count=1,
            group_count=1,
            embedding_model="embed",
            llm_model="llm",
            pipeline_version="test",
            trigger_reason="day_rollover_flush",
            generated_at=datetime(2026, 3, 31, 7, 15),
            build_metadata={},
            window_start_at=datetime(2026, 3, 30, 6, 30),
            window_end_at=datetime(2026, 3, 30, 6, 30),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        news_digests,
        "cluster_news_items",
        lambda items: [news_digests.NewsDigestCluster(items=items)],
    )

    decision = news_digests.get_news_digest_trigger_decision(
        db_session,
        user=user,
        now_utc=datetime(2026, 3, 31, 8, 0, tzinfo=UTC),
    )

    assert decision.flush_required is False
    assert decision.should_generate is False


def test_get_news_digest_trigger_decision_respects_user_interval_hours(
    db_session,
    monkeypatch,
) -> None:
    user = User(
        apple_id="interval-user",
        email="interval@example.com",
        full_name="Interval User",
        is_active=True,
        news_digest_interval_hours=3,
    )
    db_session.add(user)
    db_session.flush()

    item = _build_news_item(
        61,
        story_url="https://example.com/story-interval",
        item_url="https://news.ycombinator.com/item?id=61",
        title="Interval story",
        source_label="Hacker News",
    )
    db_session.add(item)
    db_session.flush()
    db_session.add(
        NewsDigest(
            user_id=user.id,
            timezone="UTC",
            title="Recent digest",
            summary="Generated recently.",
            source_count=1,
            group_count=1,
            embedding_model="embed",
            llm_model="llm",
            pipeline_version="test",
            trigger_reason="uncovered_item_threshold",
            generated_at=datetime(2026, 3, 31, 10, 0),
            build_metadata={},
            window_start_at=datetime(2026, 3, 31, 9, 0),
            window_end_at=datetime(2026, 3, 31, 9, 0),
        )
    )
    db_session.commit()

    monkeypatch.setattr(news_digests.get_settings(), "news_digest_min_uncovered_items", 1)
    monkeypatch.setattr(
        news_digests,
        "cluster_news_items",
        lambda items: [news_digests.NewsDigestCluster(items=items)],
    )

    decision = news_digests.get_news_digest_trigger_decision(
        db_session,
        user=user,
        now_utc=datetime(2026, 3, 31, 12, 0, tzinfo=UTC),
    )

    assert decision.trigger_reason == "uncovered_item_threshold"
    assert decision.should_generate is False
