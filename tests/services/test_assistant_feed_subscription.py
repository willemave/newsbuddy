"""Tests for assistant known-feed subscription state transitions."""

from sqlalchemy.orm import Session, sessionmaker

from app.models.db import ProcessingTask, UserScraperConfig
from app.services.assistant_feed_subscription import subscribe_known_feed


def test_assistant_known_feed_reenables_inactive_subscription(db_session, test_user) -> None:
    config = UserScraperConfig(
        user_id=test_user.id,
        scraper_type="atom",
        display_name="Paused Feed",
        config={"feed_url": "https://example.com/paused.xml", "limit": 10},
        feed_url="https://example.com/paused.xml",
        is_active=False,
    )
    db_session.add(config)
    db_session.commit()
    factory: sessionmaker[Session] = sessionmaker(bind=db_session.get_bind())

    message = subscribe_known_feed(
        factory,
        user_id=test_user.id,
        url="https://EXAMPLE.COM/paused.xml/",
        title="Paused Feed",
        feed_type="atom",
    )

    db_session.expire_all()
    persisted = db_session.get(UserScraperConfig, config.id)
    assert message == "Re-enabled Paused Feed."
    assert persisted is not None
    assert persisted.is_active is True
    assert (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == "backfill_feeds")
        .count()
        == 1
    )
