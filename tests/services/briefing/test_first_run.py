from __future__ import annotations

from datetime import UTC, datetime

from app.models.contracts import BriefingFirstRunPhase
from app.models.db import (
    BriefingLens,
    BriefingSegment,
    OnboardingFirstEditionRun,
    UserScraperConfig,
)
from app.services.briefing.first_run import (
    complete_first_edition,
    get_first_run_progress,
    mark_feed_sources_complete,
    mark_scraper_sources_complete,
    start_first_edition,
    sync_ready_categories,
)


def test_first_edition_progress_appends_sources_and_readable_categories(
    db_session,
    test_user,
) -> None:
    feed = UserScraperConfig(
        user_id=test_user.id,
        scraper_type="substack",
        display_name="Stratechery",
        feed_url="https://stratechery.com/feed/",
        config={"feed_url": "https://stratechery.com/feed/"},
    )
    aggregator = UserScraperConfig(
        user_id=test_user.id,
        scraper_type="aggregator",
        display_name="Techmeme",
        config={"key": "techmeme"},
    )
    db_session.add_all([feed, aggregator])
    db_session.flush()
    assert feed.id is not None

    run = start_first_edition(db_session, user_id=test_user.id)
    progress = get_first_run_progress(db_session, user_id=test_user.id)
    assert progress is not None
    assert progress.connected_source_count == 2
    assert progress.completed_sources == []
    assert progress.active_sources == ["Stratechery", "Techmeme"]

    assert (
        mark_scraper_sources_complete(
            db_session,
            scraper_keys=["techmeme"],
            processed_item_counts={"techmeme": 28},
        )
        == 1
    )
    progress = get_first_run_progress(db_session, user_id=test_user.id)
    assert progress is not None
    assert [source.model_dump() for source in progress.completed_sources] == [
        {"display_name": "Techmeme", "processed_item_count": 28}
    ]
    assert progress.active_sources == ["Stratechery"]

    lens = BriefingLens(
        user_id=test_user.id,
        key="technology",
        tier="news",
        title="Technology",
        deck="What changed in technology",
        position=20,
        status="active",
    )
    db_session.add(lens)
    db_session.flush()
    db_session.add(
        BriefingSegment(
            lens_id=lens.id,
            user_id=test_user.id,
            blocks=[],
            markdown_raw="Technology moved today.",
            narration_text="Technology moved today.",
            source_keys=["news:1"],
            status="active",
            model="test",
            prompt_version="test",
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    db_session.flush()

    assert sync_ready_categories(db_session, user_id=test_user.id) == 1
    progress = get_first_run_progress(db_session, user_id=test_user.id)
    assert progress is not None
    assert progress.ready_category_keys == ["technology"]
    assert progress.phase == BriefingFirstRunPhase.ACTIVE

    assert (
        mark_feed_sources_complete(
            db_session,
            user_id=test_user.id,
            config_ids=[int(feed.id)],
            processed_item_counts={int(feed.id): 12},
        )
        == 1
    )
    sync_ready_categories(db_session, user_id=test_user.id)
    progress = get_first_run_progress(db_session, user_id=test_user.id)
    assert progress is not None
    assert [source.model_dump() for source in progress.completed_sources] == [
        {"display_name": "Techmeme", "processed_item_count": 28},
        {"display_name": "Stratechery", "processed_item_count": 12},
    ]
    assert progress.phase == BriefingFirstRunPhase.READY
    assert run.status == "ready"


def test_first_edition_completion_is_durable(db_session, test_user) -> None:
    start_first_edition(db_session, user_id=test_user.id)
    assert complete_first_edition(db_session, user_id=test_user.id) is True
    assert get_first_run_progress(db_session, user_id=test_user.id) is None
    run = db_session.query(OnboardingFirstEditionRun).filter_by(user_id=test_user.id).one()
    assert run.status == "completed"
    assert run.completed_at is not None


def test_first_edition_waits_for_content_after_all_sources_finish(
    db_session,
    test_user,
) -> None:
    config = UserScraperConfig(
        user_id=test_user.id,
        scraper_type="substack",
        display_name="Platformer",
        feed_url="https://www.platformer.news/feed/",
        config={"feed_url": "https://www.platformer.news/feed/"},
    )
    db_session.add(config)
    db_session.flush()
    assert config.id is not None

    start_first_edition(db_session, user_id=test_user.id)
    assert (
        mark_feed_sources_complete(
            db_session,
            user_id=test_user.id,
            config_ids=[int(config.id)],
            processed_item_counts={int(config.id): 7},
        )
        == 1
    )

    progress = get_first_run_progress(db_session, user_id=test_user.id)
    assert progress is not None
    assert progress.phase == BriefingFirstRunPhase.WAITING_FOR_CONTENT
    assert [source.model_dump() for source in progress.completed_sources] == [
        {"display_name": "Platformer", "processed_item_count": 7}
    ]
    assert progress.ready_category_keys == []


def test_reddit_source_counts_are_attributed_to_each_config(db_session, test_user) -> None:
    machine_learning = UserScraperConfig(
        user_id=test_user.id,
        scraper_type="reddit",
        display_name="Machine Learning",
        config={"subreddit": "MachineLearning"},
    )
    local_llama = UserScraperConfig(
        user_id=test_user.id,
        scraper_type="reddit",
        display_name="Local LLaMA",
        config={"subreddit": "LocalLLaMA"},
    )
    db_session.add_all([machine_learning, local_llama])
    db_session.flush()
    assert machine_learning.id is not None
    assert local_llama.id is not None

    start_first_edition(db_session, user_id=test_user.id)
    assert (
        mark_scraper_sources_complete(
            db_session,
            scraper_keys=["reddit"],
            processed_item_counts={"reddit": 19},
            processed_item_counts_by_config_id={
                int(machine_learning.id): 11,
                int(local_llama.id): 8,
            },
        )
        == 2
    )

    progress = get_first_run_progress(db_session, user_id=test_user.id)
    assert progress is not None
    assert [source.model_dump() for source in progress.completed_sources] == [
        {"display_name": "r/MachineLearning", "processed_item_count": 11},
        {"display_name": "r/LocalLLaMA", "processed_item_count": 8},
    ]
