"""Maestro-backed iOS Briefing flows using representative long-form fixtures."""

from __future__ import annotations

import pytest

from app.core.settings import get_settings
from app.models.contracts import ContentType
from app.services.briefing.presentation import get_briefing_lens
from app.services.briefing.refresh import run_briefing_refresh
from app.services.briefing.source_keys import build_source_key

pytestmark = [pytest.mark.integration, pytest.mark.ios_e2e]


def test_briefing_routes_article_and_podcast_fixtures_to_their_lenses(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    sample_podcast,
    db_session,
    test_user,
) -> None:
    """A generated Briefing should expose representative article and podcast lenses."""
    article = create_sample_content(sample_article_long)
    podcast = create_sample_content(sample_podcast)
    assert article.id is not None
    assert podcast.id is not None
    assert test_user.id is not None
    test_user.reading_experience = "briefing"

    settings = get_settings().model_copy(
        update={
            "briefing_enabled_user_ids": [test_user.id],
            "briefing_window_min": 1,
            "briefing_debounce_seconds": 0,
            "briefing_pending_max_age_seconds": 60,
        }
    )
    refresh = run_briefing_refresh(
        db_session,
        user_id=test_user.id,
        mode="full",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    assert refresh.pending_added == 2
    assert refresh.appended_segments == 2

    podcast_lens = get_briefing_lens(db_session, user_id=test_user.id, lens_key="podcasts")
    article_lens = get_briefing_lens(db_session, user_id=test_user.id, lens_key="articles")
    assert podcast_lens is not None
    assert article_lens is not None
    assert len(podcast_lens.segments) == 1
    assert len(article_lens.segments) == 1

    podcast_key = build_source_key("content", podcast.id)
    article_key = build_source_key("content", article.id)
    assert podcast_lens.segments[0].source_keys == [podcast_key]
    assert article_lens.segments[0].source_keys == [article_key]
    assert [source.content_type for source in podcast_lens.sources] == [ContentType.PODCAST]
    assert [source.content_type for source in article_lens.sources] == [ContentType.ARTICLE]
    assert [source.title for source in podcast_lens.sources] == [podcast.title]
    assert [source.title for source in article_lens.sources] == [article.title]

    run_ios_flow(
        "briefing_article_podcast.yaml",
        extra_env={
            "ARTICLE_SEGMENT_ID": str(article_lens.segments[0].id),
            "ARTICLE_TITLE": article.title,
            "PODCAST_SEGMENT_ID": str(podcast_lens.segments[0].id),
            "PODCAST_TITLE": podcast.title,
        },
    )
