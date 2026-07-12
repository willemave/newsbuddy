"""Maestro screenshot regressions for primary iOS screens and sheets."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.db import (
    AudioEpisode,
    BriefingLens,
    BriefingSegment,
    BriefingState,
    ChatSession,
    ContentKnowledgeSave,
    LearningDeck,
    LearningDeckRun,
    NewsItem,
)
from app.services.briefing.source_keys import build_source_key
from app.utils.image_paths import get_content_images_dir, get_thumbnails_dir

pytestmark = [pytest.mark.integration, pytest.mark.ios_e2e, pytest.mark.ios_visual]

IOS_E2E_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = IOS_E2E_DIR.parent / "fixtures" / "images"
VISUAL_PROFILE = os.environ.get("NEWSLY_MAESTRO_VISUAL_PROFILE", "iphone17pro-dark")
BASELINE_DIR = Path(
    os.environ.get(
        "NEWSLY_MAESTRO_VISUAL_BASELINE_DIR",
        str(IOS_E2E_DIR / "baselines" / VISUAL_PROFILE),
    )
).resolve()

BASELINE_FILES = {
    "visual_main_screens": [
        "main-long.png",
        "main-fast.png",
        "main-knowledge.png",
        "main-learning.png",
        "main-more.png",
    ],
    "visual_briefing": [
        "briefing-articles.png",
        "briefing-news.png",
    ],
    "visual_knowledge_learning": [
        "redesign-knowledge.png",
        "redesign-learning.png",
    ],
    "visual_content_modals": [
        "detail-article.png",
        "modal-share.png",
        "modal-download.png",
        "modal-chat.png",
        "modal-learning-deck.png",
    ],
    "visual_discussion_modal": [
        "modal-discussion.png",
    ],
}

VISUAL_NOW = datetime(2026, 6, 6, 16, 41, tzinfo=UTC)
VISUAL_ARTICLE_PUBLISHED_AT = VISUAL_NOW - timedelta(hours=2, minutes=15)
VISUAL_ARTICLE_INGESTED_AT = VISUAL_ARTICLE_PUBLISHED_AT + timedelta(minutes=18)
VISUAL_ARTICLE_PROCESSED_AT = VISUAL_ARTICLE_PUBLISHED_AT + timedelta(minutes=24)
VISUAL_NEWS_PUBLISHED_AT = VISUAL_NOW - timedelta(hours=1, minutes=10)
VISUAL_NEWS_INGESTED_AT = VISUAL_NEWS_PUBLISHED_AT + timedelta(minutes=4)
VISUAL_NEWS_PROCESSED_AT = VISUAL_NEWS_PUBLISHED_AT + timedelta(minutes=9)
VISUAL_NOW_LAUNCH_VALUE = VISUAL_NOW.isoformat().replace("+00:00", "Z")


def _is_recording_baselines() -> bool:
    return os.environ.get("NEWSLY_MAESTRO_RECORD_VISUAL_BASELINES") == "1"


def _flow_name(base_name: str) -> str:
    suffix = ".record.yaml" if _is_recording_baselines() else ".yaml"
    return f"{base_name}{suffix}"


def _prepare_baselines(base_name: str) -> None:
    if _is_recording_baselines():
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        return

    missing = [
        str(BASELINE_DIR / filename)
        for filename in BASELINE_FILES[base_name]
        if not (BASELINE_DIR / filename).is_file()
    ]
    assert not missing, (
        "Missing iOS visual baselines. Record them with "
        "`NEWSLY_MAESTRO_RECORD_VISUAL_BASELINES=1 "
        "NEWSLY_MAESTRO_SIMULATOR_NAME='iPhone 17 Pro' "
        "NEWSLY_MAESTRO_APPEARANCE=dark tests/scripts/ios_maestro.sh -m ios_visual`. "
        f"Missing: {missing}"
    )


def _baseline_env() -> dict[str, str]:
    return {
        "BASELINE_DIR": str(BASELINE_DIR),
        "VISUAL_NOW": VISUAL_NOW_LAUNCH_VALUE,
    }


def _utc_naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def _apply_article_visual_timestamps(db_session, content):
    content.created_at = _utc_naive(VISUAL_ARTICLE_INGESTED_AT)
    content.updated_at = _utc_naive(VISUAL_ARTICLE_PROCESSED_AT)
    content.processed_at = _utc_naive(VISUAL_ARTICLE_PROCESSED_AT)
    content.publication_date = _utc_naive(VISUAL_ARTICLE_PUBLISHED_AT)
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)
    return content


def _seed_article_visual_artwork(content) -> None:
    """Copy deterministic visual-test artwork for the generated image URLs."""
    content_images_dir = get_content_images_dir()
    thumbnails_dir = get_thumbnails_dir()
    content_images_dir.mkdir(parents=True, exist_ok=True)
    thumbnails_dir.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(
        FIXTURES_DIR / "visual-content-article.png",
        content_images_dir / f"{content.id}.png",
    )
    shutil.copyfile(
        FIXTURES_DIR / "visual-thumbnail-article.png",
        thumbnails_dir / f"{content.id}.png",
    )


def _seed_visual_knowledge_and_learning(db_session, *, user_id: int, content) -> int:
    """Populate the redesigned tabs with stable, representative item types."""
    saved_at = _utc_naive(VISUAL_NOW - timedelta(minutes=18))
    db_session.add(
        ContentKnowledgeSave(
            user_id=user_id,
            content_id=content.id,
            saved_at=saved_at,
            created_at=saved_at,
        )
    )

    chat_session = ChatSession(
        user_id=user_id,
        content_id=content.id,
        title="How should small teams evaluate AI products?",
        session_type="knowledge_chat",
        llm_model="openai:gpt-5.5",
        llm_provider="openai",
        created_at=_utc_naive(VISUAL_NOW - timedelta(hours=2)),
        updated_at=_utc_naive(VISUAL_NOW - timedelta(minutes=12)),
        last_message_at=_utc_naive(VISUAL_NOW - timedelta(minutes=12)),
    )
    db_session.add(chat_session)

    deck_created_at = _utc_naive(VISUAL_NOW - timedelta(hours=3))
    deck = LearningDeck(
        user_id=user_id,
        source_kind="content",
        source_identity=f"content:{content.id}",
        source_url=content.url,
        source_content_id=content.id,
        source_title=content.title,
        source_metadata={"content_type": "article"},
        title="A practical playbook for evaluating AI systems",
        artifact_object_keys=[],
        share_enabled=False,
        created_at=deck_created_at,
        updated_at=_utc_naive(VISUAL_NOW - timedelta(minutes=42)),
    )
    db_session.add(deck)
    db_session.flush()
    deck_run = LearningDeckRun(
        deck_id=deck.id,
        user_id=user_id,
        status="completed",
        source_snapshot={
            "source_kind": "content",
            "source_identity": deck.source_identity,
            "source_url": deck.source_url,
            "source_title": deck.source_title,
            "source_metadata": deck.source_metadata,
        },
        timeline=[],
        artifact_object_keys=[],
        created_at=deck_created_at,
        updated_at=_utc_naive(VISUAL_NOW - timedelta(minutes=42)),
        completed_at=_utc_naive(VISUAL_NOW - timedelta(minutes=42)),
    )
    db_session.add(deck_run)
    db_session.flush()
    deck.latest_run_id = deck_run.id
    deck.latest_successful_run_id = deck_run.id
    db_session.flush()
    deck.updated_at = _utc_naive(VISUAL_NOW - timedelta(minutes=42))

    db_session.add(
        AudioEpisode(
            user_id=user_id,
            kind="custom_narration",
            status="completed",
            title="The evaluation loop, narrated",
            input_hash="ios-visual-learning-narration",
            source_item_ids=[],
            source_snapshot={
                "kind": "custom_narration",
                "content_ids": [content.id],
                "news_item_ids": [],
                "source_count": 1,
                "items": [{"content_id": content.id, "title": content.title}],
            },
            prompt_version=1,
            duration_seconds=488,
            created_at=_utc_naive(VISUAL_NOW - timedelta(days=1, hours=1)),
            updated_at=_utc_naive(VISUAL_NOW - timedelta(days=1)),
        )
    )
    db_session.commit()
    db_session.refresh(chat_session)
    assert chat_session.id is not None
    return int(chat_session.id)


def _create_user_visible_news_item(
    db_session,
    *,
    user_id: int,
    ingest_key: str,
    title: str,
    discussion_payload: dict | None = None,
) -> NewsItem:
    summary_text = (
        "Developers compare compact homelab servers built around Intel's efficient "
        "N100 and N150 chips."
    )
    key_points = [
        "Several compact NAS systems now pair efficient Intel chips with multiple NVMe slots.",
        (
            "The discussion weighs power draw, network throughput, and storage "
            "tradeoffs for home labs."
        ),
    ]
    item = NewsItem(
        ingest_key=ingest_key,
        visibility_scope="user",
        owner_user_id=user_id,
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id=ingest_key,
        canonical_item_url="https://news.ycombinator.com/item?id=424242",
        canonical_story_url="https://example.com/visual-regression-story",
        article_url="https://example.com/visual-regression-story",
        article_title=title,
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=424242",
        summary_title=title,
        summary_key_points=key_points,
        summary_text=summary_text,
        raw_metadata={
            "discussion_url": "https://news.ycombinator.com/item?id=424242",
            "summary": {
                "article_url": "https://example.com/visual-regression-story",
                "summary": summary_text,
                "key_points": key_points,
            },
            "discussion_payload": discussion_payload or {},
        },
        status="ready",
        published_at=_utc_naive(VISUAL_NEWS_PUBLISHED_AT),
        ingested_at=_utc_naive(VISUAL_NEWS_INGESTED_AT),
        processed_at=_utc_naive(VISUAL_NEWS_PROCESSED_AT),
        created_at=_utc_naive(VISUAL_NEWS_INGESTED_AT),
        updated_at=_utc_naive(VISUAL_NEWS_PROCESSED_AT),
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def _seed_visual_briefing(
    db_session,
    *,
    user_id: int,
    content,
    news_item: NewsItem,
) -> tuple[int, int]:
    state = BriefingState(
        user_id=user_id,
        version=7,
        masthead_title="Briefing",
        masthead_deck="A stable visual-test edition for the new briefing experience.",
        last_append_at=_utc_naive(VISUAL_NOW),
    )
    articles_lens = BriefingLens(
        user_id=user_id,
        key="articles",
        tier="longform",
        title="Articles",
        deck="Long reads with enough context to decide what deserves time.",
        position=10,
        status="active",
    )
    news_lens = BriefingLens(
        user_id=user_id,
        key="ai-hardware",
        tier="news",
        title="AI Hardware",
        deck="Chips, servers, and the supply chain around model deployment.",
        position=20,
        status="active",
    )
    db_session.add_all([state, articles_lens, news_lens])
    db_session.flush()

    assert content.id is not None
    assert news_item.id is not None
    assert articles_lens.id is not None
    assert news_lens.id is not None
    content_key = build_source_key("content", content.id)
    news_key = build_source_key("news", news_item.id)
    article_segment = BriefingSegment(
        lens_id=articles_lens.id,
        user_id=user_id,
        blocks=[
            {
                "type": "passage",
                "weight": "lead",
                "paragraphs": [
                    {
                        "runs": [
                            {
                                "kind": "text",
                                "text": "Start with ",
                            },
                            {
                                "kind": "source_link",
                                "text": content.title,
                                "source_key": content_key,
                            },
                            {
                                "kind": "text",
                                "text": (
                                    ": the piece connects small-team tooling, evaluation loops, "
                                    "and the decisions that make AI products feel less brittle."
                                ),
                            },
                        ]
                    }
                ],
            },
            {
                "type": "figure",
                "source_key": content_key,
                "caption": "A compact systems map from the article.",
                "placement": "full",
            },
            {
                "type": "pullquote",
                "source_key": content_key,
                "text": "The best systems make the next review easier than the first one.",
            },
            {
                "type": "passage",
                "paragraphs": [
                    {
                        "runs": [
                            {
                                "kind": "text",
                                "text": (
                                    "The useful bit for today is operational: treat evaluation, "
                                    "routing, and human review as one product surface."
                                ),
                            }
                        ]
                    }
                ],
            },
        ],
        markdown_raw=f"[{content.title}](newsly://briefing/content/{content.id})",
        narration_text="A compact briefing about product evaluation loops.",
        source_keys=[content_key],
        status="active",
        model="visual-fixture",
        prompt_version="test",
        created_at=_utc_naive(VISUAL_NOW - timedelta(minutes=18)),
        updated_at=_utc_naive(VISUAL_NOW - timedelta(minutes=18)),
    )
    news_segment = BriefingSegment(
        lens_id=news_lens.id,
        user_id=user_id,
        blocks=[
            {
                "type": "passage",
                "weight": "lead",
                "paragraphs": [
                    {
                        "runs": [
                            {
                                "kind": "source_link",
                                "text": news_item.summary_title,
                                "source_key": news_key,
                            },
                            {
                                "kind": "text",
                                "text": (
                                    " keeps the hardware story grounded in practical bottlenecks: "
                                    "storage, power draw, networking, and small-cluster costs."
                                ),
                            },
                        ]
                    }
                ],
            },
            {
                "type": "pullquote",
                "source_key": news_key,
                "text": "Small boxes are becoming credible infrastructure, not just hobby gear.",
            },
            {
                "type": "passage",
                "paragraphs": [
                    {
                        "runs": [
                            {
                                "kind": "text",
                                "text": (
                                    "Watch whether these boards stay niche or become the default "
                                    "edge lab for teams testing retrieval-heavy workloads."
                                ),
                            }
                        ]
                    }
                ],
            },
        ],
        markdown_raw=f"[{news_item.summary_title}](newsly://briefing/news/{news_item.id})",
        narration_text="A short briefing about compact AI hardware.",
        source_keys=[news_key],
        status="active",
        model="visual-fixture",
        prompt_version="test",
        created_at=_utc_naive(VISUAL_NOW - timedelta(minutes=9)),
        updated_at=_utc_naive(VISUAL_NOW - timedelta(minutes=9)),
    )
    db_session.add_all([article_segment, news_segment])
    db_session.commit()
    db_session.refresh(article_segment)
    db_session.refresh(news_segment)
    assert article_segment.id is not None
    assert news_segment.id is not None
    return int(article_segment.id), int(news_segment.id)


def test_primary_tabs_match_visual_baselines(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    db_session,
    test_user,
) -> None:
    """Primary tab screens should keep their known visual shape."""
    _prepare_baselines("visual_main_screens")
    long_content = create_sample_content(sample_article_long)
    long_content = _apply_article_visual_timestamps(db_session, long_content)
    _seed_article_visual_artwork(long_content)
    chat_session_id = _seed_visual_knowledge_and_learning(
        db_session,
        user_id=test_user.id,
        content=long_content,
    )
    news_item = _create_user_visible_news_item(
        db_session,
        user_id=test_user.id,
        ingest_key="ios-visual-main-screen",
        title="Mini NAS Boards Put NVMe Storage in Tiny Homelab Servers",
    )

    run_ios_flow(
        _flow_name("visual_main_screens"),
        extra_env={
            **_baseline_env(),
            "LONG_CONTENT_ID": str(long_content.id),
            "NEWS_ITEM_ID": str(news_item.id),
            "CHAT_SESSION_ID": str(chat_session_id),
        },
    )


def test_briefing_experience_matches_visual_baselines(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    db_session,
    test_user,
) -> None:
    """Briefing article and news lenses should keep their known visual shape."""
    _prepare_baselines("visual_briefing")
    test_user.reading_experience = "briefing"
    content = create_sample_content(sample_article_long)
    content = _apply_article_visual_timestamps(db_session, content)
    _seed_article_visual_artwork(content)
    news_item = _create_user_visible_news_item(
        db_session,
        user_id=test_user.id,
        ingest_key="ios-visual-briefing-news",
        title="Compact AI Hardware Turns Homelab Boards Into Real Test Beds",
    )
    article_segment_id, news_segment_id = _seed_visual_briefing(
        db_session,
        user_id=test_user.id,
        content=content,
        news_item=news_item,
    )

    run_ios_flow(
        _flow_name("visual_briefing"),
        extra_env={
            **_baseline_env(),
            "ARTICLE_SEGMENT_ID": str(article_segment_id),
            "NEWS_SEGMENT_ID": str(news_segment_id),
        },
    )


def test_knowledge_learning_tabs_match_visual_baselines(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    db_session,
    test_user,
) -> None:
    """The Briefing experience should expose the visual Knowledge and Learning roots."""
    _prepare_baselines("visual_knowledge_learning")
    test_user.reading_experience = "briefing"
    content = create_sample_content(sample_article_long)
    content = _apply_article_visual_timestamps(db_session, content)
    _seed_article_visual_artwork(content)
    chat_session_id = _seed_visual_knowledge_and_learning(
        db_session,
        user_id=test_user.id,
        content=content,
    )

    run_ios_flow(
        _flow_name("visual_knowledge_learning"),
        extra_env={
            **_baseline_env(),
            "LONG_CONTENT_ID": str(content.id),
            "CHAT_SESSION_ID": str(chat_session_id),
        },
    )


def test_content_detail_modals_match_visual_baselines(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    db_session,
) -> None:
    """Article detail action sheets should stay compact and visually stable."""
    _prepare_baselines("visual_content_modals")
    content = create_sample_content(sample_article_long)
    content = _apply_article_visual_timestamps(db_session, content)
    _seed_article_visual_artwork(content)

    run_ios_flow(
        _flow_name("visual_content_modals"),
        extra_env={**_baseline_env(), "CONTENT_ID": str(content.id)},
    )


def test_discussion_modal_matches_visual_baseline(
    run_ios_flow,
    db_session,
    test_user,
) -> None:
    """Discussion sheets should render comments without oversized empty detents."""
    _prepare_baselines("visual_discussion_modal")
    comment_id = "visual-comment-1"
    news_item = _create_user_visible_news_item(
        db_session,
        user_id=test_user.id,
        ingest_key="ios-visual-discussion-modal",
        title="Mini NAS Boards Put NVMe Storage in Tiny Homelab Servers",
        discussion_payload={
            "mode": "comments",
            "source_url": "https://news.ycombinator.com/item?id=424242",
            "comments": [
                {
                    "comment_id": comment_id,
                    "author": "alice",
                    "text": (
                        "The compact sheet is easier to scan and should not leave a "
                        "huge empty tail."
                    ),
                    "compact_text": (
                        "The compact sheet is easier to scan and should not leave a "
                        "huge empty tail."
                    ),
                    "depth": 0,
                }
            ],
            "discussion_groups": [],
            "links": [],
            "summary": {
                "overview": (
                    "Commenters focus on the practical tradeoffs of small NAS builds, "
                    "including thermals, networking, and drive layout."
                ),
                "topics": [
                    {
                        "title": "Build tradeoffs",
                        "summary": (
                            "The thread weighs compact hardware convenience against "
                            "cooling, networking, and expandability."
                        ),
                    }
                ],
                "notable_links": [],
                "representative_comments": [],
                "external_discussion_url": "https://news.ycombinator.com/item?id=424242",
            },
            "stats": {"fetched_count": 1},
        },
    )

    run_ios_flow(
        _flow_name("visual_discussion_modal"),
        extra_env={
            **_baseline_env(),
            "NEWS_ITEM_ID": str(news_item.id),
            "COMMENT_ID": comment_id,
        },
    )
