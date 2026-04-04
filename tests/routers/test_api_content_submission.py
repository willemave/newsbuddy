"""Tests for user-submitted content endpoint."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.constants import SELF_SUBMISSION_SOURCE
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content, ContentReadStatus, ContentStatusEntry, ProcessingTask
from app.services.queue import TaskQueue, TaskStatus, TaskType


def test_submit_url_creates_content_and_analyze_task(client, db_session):
    """Submitting a new URL should persist content with UNKNOWN type and enqueue ANALYZE_URL."""
    response = client.post("/api/content/submit", json={"url": "https://example.com/article"})

    assert response.status_code == 201
    data = response.json()

    # New submissions always have UNKNOWN type until analyzed
    assert data["content_type"] == ContentType.UNKNOWN.value
    assert data["already_exists"] is False
    assert data["source"] == SELF_SUBMISSION_SOURCE
    assert data["message"] == "Content queued for analysis"

    created = db_session.query(Content).filter(Content.id == data["content_id"]).first()
    assert created is not None
    assert created.source == SELF_SUBMISSION_SOURCE
    assert created.status == ContentStatus.NEW.value
    assert created.content_type == ContentType.UNKNOWN.value
    assert created.classification == "to_read"

    # Task should be ANALYZE_URL, not PROCESS_CONTENT
    task = db_session.query(ProcessingTask).filter_by(content_id=created.id).first()
    assert task is not None
    assert task.task_type == TaskType.ANALYZE_URL.value
    assert task.queue_name == TaskQueue.CONTENT.value
    assert task.status == TaskStatus.PENDING.value


def test_duplicate_submission_reuses_existing_record(client, db_session):
    """Submitting the same URL should reuse the record and avoid duplicates."""
    existing = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.NEW.value,
        source=SELF_SUBMISSION_SOURCE,
    )
    db_session.add(existing)
    db_session.commit()

    response = client.post(
        "/api/content/submit",
        json={"url": existing.url},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["already_exists"] is True
    assert data["content_id"] == existing.id
    # Existing content keeps its type
    assert data["content_type"] == ContentType.ARTICLE.value

    contents = db_session.query(Content).filter(Content.url == existing.url).all()
    assert len(contents) == 1

    # Should have either ANALYZE_URL or PROCESS_CONTENT task
    tasks = (
        db_session.query(ProcessingTask)
        .filter_by(content_id=existing.id)
        .filter(
            ProcessingTask.task_type.in_(
                [TaskType.ANALYZE_URL.value, TaskType.PROCESS_CONTENT.value]
            )
        )
        .all()
    )
    assert len(tasks) == 1


def test_duplicate_completed_submission_does_not_reanalyze_without_new_inputs(
    client,
    db_session,
):
    """Completed content should not enqueue ANALYZE_URL again on a plain duplicate submit."""
    existing = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        source=SELF_SUBMISSION_SOURCE,
    )
    db_session.add(existing)
    db_session.commit()

    response = client.post(
        "/api/content/submit",
        json={"url": existing.url},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["already_exists"] is True
    assert data["task_id"] is None

    tasks = (
        db_session.query(ProcessingTask)
        .filter_by(content_id=existing.id)
        .filter(
            ProcessingTask.task_type.in_(
                [TaskType.ANALYZE_URL.value, TaskType.PROCESS_CONTENT.value]
            )
        )
        .all()
    )
    assert tasks == []


def test_duplicate_processing_submission_does_not_reanalyze_without_new_inputs(
    client,
    db_session,
):
    """Processing content should keep its current work instead of restarting analysis."""
    existing = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.PROCESSING.value,
        source=SELF_SUBMISSION_SOURCE,
    )
    db_session.add(existing)
    db_session.commit()

    response = client.post(
        "/api/content/submit",
        json={"url": existing.url},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["already_exists"] is True
    assert data["task_id"] is None

    tasks = (
        db_session.query(ProcessingTask)
        .filter_by(content_id=existing.id)
        .filter(
            ProcessingTask.task_type.in_(
                [TaskType.ANALYZE_URL.value, TaskType.PROCESS_CONTENT.value]
            )
        )
        .all()
    )
    assert tasks == []


def test_submit_spotify_url_creates_unknown_type(client, db_session):
    """Spotify URLs are submitted with UNKNOWN type; type detection happens async."""
    response = client.post(
        "/api/content/submit",
        json={"url": "https://open.spotify.com/episode/abcdef"},
    )

    assert response.status_code == 201
    data = response.json()
    # All new submissions have UNKNOWN type - ANALYZE_URL task will determine actual type
    assert data["content_type"] == ContentType.UNKNOWN.value
    # Platform is not set until ANALYZE_URL task runs
    assert data["platform"] is None


def test_submit_accepts_instruction_alias(client, db_session):
    """Instruction/note field should be accepted and added to ANALYZE_URL payload."""
    response = client.post(
        "/api/content/submit",
        json={
            "url": "https://example.com/article",
            "note": "Add all links from the page",
        },
    )

    assert response.status_code == 201
    data = response.json()

    created = db_session.query(Content).filter(Content.id == data["content_id"]).first()
    assert created is not None
    assert "instruction" not in (created.content_metadata or {})

    task = db_session.query(ProcessingTask).filter_by(content_id=created.id).first()
    assert task is not None
    assert task.payload.get("instruction") == "Add all links from the page"


def test_submit_with_crawl_links_sets_payload_flag(client, db_session):
    """Submitting with crawl_links should persist the flag in the ANALYZE_URL payload."""
    response = client.post(
        "/api/content/submit",
        json={
            "url": "https://example.com/article",
            "crawl_links": True,
        },
    )

    assert response.status_code == 201
    data = response.json()

    task = db_session.query(ProcessingTask).filter_by(content_id=data["content_id"]).first()
    assert task is not None
    assert task.payload.get("crawl_links") is True


def test_submit_with_subscribe_to_feed_skips_inbox_and_sets_payload(
    client,
    db_session,
    test_user,
):
    """Submitting with subscribe_to_feed should skip inbox and set payload flag."""
    response = client.post(
        "/api/content/submit",
        json={
            "url": "https://example.com/article",
            "subscribe_to_feed": True,
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["message"] == "Feed subscription queued"

    created = db_session.query(Content).filter(Content.id == data["content_id"]).first()
    assert created is not None
    assert created.content_metadata.get("subscribe_to_feed") is True

    status_entry = (
        db_session.query(ContentStatusEntry)
        .filter(
            ContentStatusEntry.user_id == test_user.id,
            ContentStatusEntry.content_id == created.id,
        )
        .first()
    )
    assert status_entry is None

    task = db_session.query(ProcessingTask).filter_by(content_id=created.id).first()
    assert task is not None
    assert task.payload.get("subscribe_to_feed") is True


def test_existing_submission_with_subscribe_to_feed_reuses_record_and_sets_feed_flag(
    client,
    db_session,
    test_user,
):
    """Existing content should be reused and updated for feed subscription mode."""
    existing = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.NEW.value,
        source=SELF_SUBMISSION_SOURCE,
        content_metadata={},
    )
    db_session.add(existing)
    db_session.commit()

    response = client.post(
        "/api/content/submit",
        json={"url": existing.url, "subscribe_to_feed": True},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["already_exists"] is True
    assert data["message"] == "Feed subscription queued"

    db_session.refresh(existing)
    assert existing.content_metadata.get("subscribe_to_feed") is True

    status_entry = (
        db_session.query(ContentStatusEntry)
        .filter(
            ContentStatusEntry.user_id == test_user.id,
            ContentStatusEntry.content_id == existing.id,
        )
        .first()
    )
    assert status_entry is None

    task = db_session.query(ProcessingTask).filter_by(content_id=existing.id).first()
    assert task is not None
    assert task.payload.get("subscribe_to_feed") is True


def test_existing_pending_analyze_task_gets_subscribe_to_feed_flag(client, db_session):
    """Existing pending analyze tasks should merge a later feed-subscribe request."""
    existing = Content(
        url="https://example.com/article",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.NEW.value,
        source=SELF_SUBMISSION_SOURCE,
        content_metadata={},
    )
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)

    task = ProcessingTask(
        task_type=TaskType.ANALYZE_URL.value,
        content_id=existing.id,
        payload={"content_id": existing.id},
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    db_session.add(task)
    db_session.commit()

    response = client.post(
        "/api/content/submit",
        json={"url": existing.url, "subscribe_to_feed": True},
    )

    assert response.status_code == 200
    db_session.refresh(task)
    assert task.payload.get("subscribe_to_feed") is True


def test_existing_pending_analyze_task_merges_instruction_crawl_links_and_platform_hint(
    client,
    db_session,
):
    """Existing pending analyze tasks should merge new analysis inputs without duplicating work."""
    existing = Content(
        url="https://example.com/article",
        source_url=None,
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.NEW.value,
        source=SELF_SUBMISSION_SOURCE,
        platform=None,
        content_metadata={},
    )
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)

    task = ProcessingTask(
        task_type=TaskType.ANALYZE_URL.value,
        content_id=existing.id,
        payload={"content_id": existing.id},
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    db_session.add(task)
    db_session.commit()

    response = client.post(
        "/api/content/submit",
        json={
            "url": existing.url,
            "note": "Add all links from the page",
            "crawl_links": True,
            "platform": "YouTube",
        },
    )

    assert response.status_code == 200
    assert response.json()["task_id"] == task.id

    db_session.refresh(existing)
    db_session.refresh(task)
    assert existing.source_url == existing.url
    assert existing.platform == "youtube"
    assert task.payload["content_id"] == existing.id
    assert task.payload["instruction"] == "Add all links from the page"
    assert task.payload["crawl_links"] is True


def test_duplicate_completed_submission_updates_source_url_and_platform_without_reanalyzing(
    client,
    db_session,
):
    """Completed content should absorb missing metadata without restarting analysis."""
    existing = Content(
        url="https://example.com/article",
        source_url=None,
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        source=SELF_SUBMISSION_SOURCE,
        platform=None,
        content_metadata={},
    )
    db_session.add(existing)
    db_session.commit()

    response = client.post(
        "/api/content/submit",
        json={
            "url": existing.url,
            "platform": "YouTube",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["already_exists"] is True
    assert data["task_id"] is None
    assert data["platform"] == "youtube"

    db_session.refresh(existing)
    assert existing.source_url == existing.url
    assert existing.platform == "youtube"

    tasks = (
        db_session.query(ProcessingTask)
        .filter_by(content_id=existing.id)
        .filter(
            ProcessingTask.task_type.in_(
                [TaskType.ANALYZE_URL.value, TaskType.PROCESS_CONTENT.value]
            )
        )
        .all()
    )
    assert tasks == []


def test_submit_duplicate_constraint_reuses_existing_record_and_enqueues_task(
    client,
    db_session,
    monkeypatch,
):
    """A duplicate insert race should fall back to the existing row."""
    original_commit = db_session.commit
    commit_calls = 0

    def _commit_with_duplicate() -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls != 1:
            original_commit()
            return

        competing_session = sessionmaker(bind=db_session.get_bind())()
        try:
            competing_session.add(
                Content(
                    url="https://example.com/article",
                    source_url="https://example.com/article",
                    content_type=ContentType.UNKNOWN.value,
                    status=ContentStatus.NEW.value,
                    source=SELF_SUBMISSION_SOURCE,
                    content_metadata={},
                )
            )
            competing_session.commit()
        finally:
            competing_session.close()

        raise IntegrityError("INSERT INTO contents", {}, Exception("duplicate key"))

    monkeypatch.setattr(db_session, "commit", _commit_with_duplicate)

    response = client.post("/api/content/submit", json={"url": "https://example.com/article"})

    assert response.status_code == 200
    data = response.json()
    assert data["already_exists"] is True
    assert data["content_type"] == ContentType.UNKNOWN.value
    assert data["task_id"] is not None

    contents = db_session.query(Content).filter(Content.url == "https://example.com/article").all()
    assert len(contents) == 1

    task = db_session.query(ProcessingTask).filter_by(content_id=contents[0].id).one()
    assert task.task_type == TaskType.ANALYZE_URL.value


def test_reject_invalid_scheme(client):
    """Non-http(s) schemes should fail validation."""
    response = client.post("/api/content/submit", json={"url": "ftp://example.com/file"})

    assert response.status_code == 422


def test_submit_share_and_chat_marks_read_and_tracks_user(client, db_session, test_user):
    """Submitting with share_and_chat should mark content as read and track the user."""
    response = client.post(
        "/api/content/submit",
        json={"url": "https://example.com/article", "share_and_chat": True},
    )

    assert response.status_code == 201
    data = response.json()

    created = db_session.query(Content).filter(Content.id == data["content_id"]).first()
    assert created is not None
    assert created.content_metadata.get("share_and_chat_user_ids") == [test_user.id]

    read_status_row = (
        db_session.query(ContentReadStatus)
        .filter(
            ContentReadStatus.user_id == test_user.id,
            ContentReadStatus.content_id == created.id,
        )
        .first()
    )
    assert read_status_row is not None


def test_share_and_chat_existing_completed_enqueues_dig_deeper_task(client, db_session, test_user):
    """Completed content should enqueue dig-deeper immediately for share_and_chat."""
    existing = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        source=SELF_SUBMISSION_SOURCE,
        content_metadata={},
    )
    db_session.add(existing)
    db_session.commit()

    response = client.post(
        "/api/content/submit",
        json={"url": existing.url, "share_and_chat": True},
    )

    assert response.status_code == 200

    task = (
        db_session.query(ProcessingTask)
        .filter_by(content_id=existing.id, task_type=TaskType.DIG_DEEPER.value)
        .first()
    )
    assert task is not None
    assert task.queue_name == TaskQueue.CHAT.value
