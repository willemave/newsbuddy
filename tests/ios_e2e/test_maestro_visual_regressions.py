"""Maestro screenshot regressions for primary iOS screens and sheets."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models.db import NewsItem

pytestmark = [pytest.mark.integration, pytest.mark.ios_e2e, pytest.mark.ios_visual]

IOS_E2E_DIR = Path(__file__).resolve().parent
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
        "main-more.png",
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
    return {"BASELINE_DIR": str(BASELINE_DIR)}


def _create_user_visible_news_item(
    db_session,
    *,
    user_id: int,
    ingest_key: str,
    title: str,
    discussion_payload: dict | None = None,
) -> NewsItem:
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
        summary_key_points=[
            "The visual regression fixture keeps Fast News deterministic.",
            "The sheet should be compact and free of large empty lower regions.",
        ],
        summary_text="A deterministic Fast News item for visual regression coverage.",
        raw_metadata={
            "discussion_url": "https://news.ycombinator.com/item?id=424242",
            "summary": {
                "article_url": "https://example.com/visual-regression-story",
                "summary": "A deterministic Fast News item for visual regression coverage.",
                "key_points": [
                    "The visual regression fixture keeps Fast News deterministic.",
                    "The sheet should be compact and free of large empty lower regions.",
                ],
            },
            "discussion_payload": discussion_payload or {},
        },
        status="ready",
        published_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC).replace(tzinfo=None),
        ingested_at=datetime(2026, 1, 15, 12, 5, tzinfo=UTC).replace(tzinfo=None),
        processed_at=datetime(2026, 1, 15, 12, 10, tzinfo=UTC).replace(tzinfo=None),
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


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
    news_item = _create_user_visible_news_item(
        db_session,
        user_id=test_user.id,
        ingest_key="ios-visual-main-screen",
        title="Visual Regression Main Screen Fixture",
    )

    run_ios_flow(
        _flow_name("visual_main_screens"),
        extra_env={
            **_baseline_env(),
            "LONG_CONTENT_ID": str(long_content.id),
            "NEWS_ITEM_ID": str(news_item.id),
        },
    )


def test_content_detail_modals_match_visual_baselines(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
) -> None:
    """Article detail action sheets should stay compact and visually stable."""
    _prepare_baselines("visual_content_modals")
    content = create_sample_content(sample_article_long)

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
        title="Visual Regression Discussion Fixture",
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
