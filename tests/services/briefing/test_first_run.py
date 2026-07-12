from __future__ import annotations

from app.models.contracts import BriefingFirstRunPhase, BriefingFirstRunSourceOutcome
from app.models.db import (
    OnboardingFirstEditionRun,
    OnboardingFirstEditionSource,
    UserScraperConfig,
)
from app.services.briefing.first_run import (
    complete_first_edition,
    get_first_run_progress,
    record_feed_source_result,
    record_scraper_source_result,
    start_first_edition,
)


def test_first_edition_progress_appends_sources_and_uses_ready_briefing_categories(
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
    assert run.id is not None
    progress = get_first_run_progress(db_session, user_id=test_user.id)
    assert progress is not None
    assert progress.run_id == run.id
    assert progress.connected_source_count == 2
    assert progress.completed_sources == []
    assert progress.active_sources == ["Stratechery", "Techmeme"]

    assert (
        record_scraper_source_result(
            db_session,
            run_id=run.id,
            scraper_key="techmeme",
            processed_item_count=28,
            processed_item_counts_by_config_id=None,
            outcome=BriefingFirstRunSourceOutcome.PROCESSED,
        )
        == 1
    )
    progress = get_first_run_progress(
        db_session,
        user_id=test_user.id,
        ready_category_keys=["technology"],
    )
    assert progress is not None
    assert [source.model_dump(mode="json") for source in progress.completed_sources] == [
        {
            "display_name": "Techmeme",
            "processed_item_count": 28,
            "outcome": "processed",
        }
    ]
    assert progress.active_sources == ["Stratechery"]
    assert progress.ready_category_keys == ["technology"]
    assert progress.phase == BriefingFirstRunPhase.ACTIVE

    assert record_feed_source_result(
        db_session,
        run_id=run.id,
        config_id=feed.id,
        processed_item_count=12,
        outcome=BriefingFirstRunSourceOutcome.PROCESSED,
    )
    progress = get_first_run_progress(
        db_session,
        user_id=test_user.id,
        ready_category_keys=["technology"],
    )
    assert progress is not None
    assert [source.display_name for source in progress.completed_sources] == [
        "Techmeme",
        "Stratechery",
    ]
    assert progress.phase == BriefingFirstRunPhase.READY
    assert run.status == "active"
    assert run.revision == 3


def test_first_edition_completion_is_durable(db_session, test_user) -> None:
    start_first_edition(db_session, user_id=test_user.id)
    assert complete_first_edition(db_session, user_id=test_user.id) is True
    assert get_first_run_progress(db_session, user_id=test_user.id) is None
    run = db_session.query(OnboardingFirstEditionRun).filter_by(user_id=test_user.id).one()
    assert run.status == "completed"
    assert run.completed_at is not None


def test_unavailable_source_is_terminal_while_content_is_pending(db_session, test_user) -> None:
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

    run = start_first_edition(db_session, user_id=test_user.id)
    assert run.id is not None
    assert record_feed_source_result(
        db_session,
        run_id=run.id,
        config_id=config.id,
        processed_item_count=0,
        outcome=BriefingFirstRunSourceOutcome.UNAVAILABLE,
    )

    progress = get_first_run_progress(db_session, user_id=test_user.id)
    assert progress is not None
    assert progress.phase == BriefingFirstRunPhase.WAITING_FOR_CONTENT
    assert progress.completed_sources[0].outcome == BriefingFirstRunSourceOutcome.UNAVAILABLE
    assert progress.active_sources == []
    source_row = (
        db_session.query(OnboardingFirstEditionSource)
        .filter(OnboardingFirstEditionSource.run_id == run.id)
        .one()
    )
    initial_completed_at = source_row.completed_at
    assert initial_completed_at is not None

    revision_after_failure = run.revision
    assert not record_feed_source_result(
        db_session,
        run_id=run.id,
        config_id=config.id,
        processed_item_count=0,
        outcome=BriefingFirstRunSourceOutcome.UNAVAILABLE,
    )
    assert run.revision == revision_after_failure

    assert record_feed_source_result(
        db_session,
        run_id=run.id,
        config_id=config.id,
        processed_item_count=5,
        outcome=BriefingFirstRunSourceOutcome.PROCESSED,
    )
    assert not record_feed_source_result(
        db_session,
        run_id=run.id,
        config_id=config.id,
        processed_item_count=0,
        outcome=BriefingFirstRunSourceOutcome.UNAVAILABLE,
    )
    progress = get_first_run_progress(db_session, user_id=test_user.id)
    assert progress is not None
    assert progress.completed_sources[0].outcome == BriefingFirstRunSourceOutcome.PROCESSED
    assert progress.completed_sources[0].processed_item_count == 5
    assert source_row.completed_at == initial_completed_at


def test_scraper_result_is_scoped_to_the_originating_run(db_session, test_user) -> None:
    config = UserScraperConfig(
        user_id=test_user.id,
        scraper_type="reddit",
        display_name="Machine Learning",
        config={"subreddit": "MachineLearning"},
    )
    db_session.add(config)
    db_session.flush()
    assert config.id is not None

    expired_run = start_first_edition(db_session, user_id=test_user.id)
    assert expired_run.id is not None
    active_run = start_first_edition(db_session, user_id=test_user.id)
    assert active_run.id is not None

    assert (
        record_scraper_source_result(
            db_session,
            run_id=expired_run.id,
            scraper_key="reddit",
            processed_item_count=11,
            processed_item_counts_by_config_id={config.id: 11},
            outcome=BriefingFirstRunSourceOutcome.PROCESSED,
        )
        == 0
    )
    progress = get_first_run_progress(db_session, user_id=test_user.id)
    assert progress is not None
    assert progress.run_id == active_run.id
    assert progress.completed_sources == []


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

    run = start_first_edition(db_session, user_id=test_user.id)
    assert run.id is not None
    assert (
        record_scraper_source_result(
            db_session,
            run_id=run.id,
            scraper_key="reddit",
            processed_item_count=19,
            processed_item_counts_by_config_id={
                machine_learning.id: 11,
                local_llama.id: 8,
            },
            outcome=BriefingFirstRunSourceOutcome.PROCESSED,
        )
        == 2
    )

    progress = get_first_run_progress(db_session, user_id=test_user.id)
    assert progress is not None
    assert [source.model_dump(mode="json") for source in progress.completed_sources] == [
        {
            "display_name": "r/MachineLearning",
            "processed_item_count": 11,
            "outcome": "processed",
        },
        {
            "display_name": "r/LocalLLaMA",
            "processed_item_count": 8,
            "outcome": "processed",
        },
    ]
