from app.models.db import ProcessingTask
from app.services.onboarding.completion_tasks import (
    _has_feed_discovery_task,
    build_onboarding_completion_task_batch,
)
from app.services.queue import TaskType


def test_feed_discovery_task_lookup_uses_indexed_owner_instead_of_payload(
    db_session,
    test_user,
    user_factory,
) -> None:
    other_user = user_factory()
    db_session.add(
        ProcessingTask(
            owner_user_id=other_user.id,
            task_type=TaskType.DISCOVER_FEEDS.value,
            payload={"user_id": test_user.id},
            status="completed",
            queue_name="content",
        )
    )
    db_session.commit()

    assert _has_feed_discovery_task(db_session, user_id=test_user.id) is False

    db_session.add(
        ProcessingTask(
            owner_user_id=test_user.id,
            task_type=TaskType.DISCOVER_FEEDS.value,
            payload={},
            status="completed",
            queue_name="content",
        )
    )
    db_session.commit()

    assert _has_feed_discovery_task(db_session, user_id=test_user.id) is True


def test_ownerless_onboarding_scrape_grants_requesting_user_access(
    db_session,
    test_user,
) -> None:
    requests, response_task_index = build_onboarding_completion_task_batch(
        db_session,
        user_id=test_user.id,
        feed_config_ids=[],
        sources_to_scrape=["reddit"],
        first_edition_run_id=42,
        discovery_payload=None,
        seeded_feed_content_ids=[],
    )

    scrape_request = next(request for request in requests if request.task_type == TaskType.SCRAPE)
    assert response_task_index == requests.index(scrape_request)
    assert scrape_request.owner_user_id is None
    assert scrape_request.access_user_id == test_user.id
