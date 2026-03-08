from app.models.contracts import TaskStatus, TaskType
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content, ContentStatusEntry, ProcessingTask
from app.services.long_form_images import (
    CANCELLED_NOT_VISIBLE_UNDER_FEED_RULES,
    cancel_ineligible_pending_generate_image_tasks,
    enqueue_visible_long_form_image_if_needed,
    is_visible_long_form_image_candidate,
)


def _build_article_summary(title: str) -> dict[str, object]:
    return {
        "title": title,
        "overview": (
            "This overview is long enough to satisfy the minimum length requirement "
            "for structured summaries."
        ),
        "bullet_points": [
            {"text": "Key point one", "category": "key_finding"},
            {"text": "Key point two", "category": "methodology"},
            {"text": "Key point three", "category": "conclusion"},
        ],
        "quotes": [],
        "topics": ["Testing"],
    }


def _build_podcast_summary(title: str) -> dict[str, object]:
    return {
        "title": title,
        "editorial_narrative": (
            "First paragraph with concrete details and practical implications.\n\n"
            "Second paragraph with tradeoffs and operational detail."
        ),
        "quotes": [{"text": "A quote", "attribution": "Host"}],
        "key_points": [
            {"point": "Point one with concrete detail."},
            {"point": "Point two with concrete detail."},
            {"point": "Point three with concrete detail."},
            {"point": "Point four with concrete detail."},
        ],
    }


class DummyQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[TaskType, int | None]] = []

    def enqueue(
        self,
        task_type: TaskType,
        content_id: int | None = None,
        payload: dict | None = None,
        queue_name=None,
        dedupe: bool | None = None,
    ) -> int:
        _ = payload, queue_name, dedupe
        self.calls.append((task_type, content_id))
        return len(self.calls)


def test_visible_completed_article_is_eligible_for_generated_image(db_session, test_user) -> None:
    content = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "summary": _build_article_summary("Visible article"),
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.add(
        ContentStatusEntry(user_id=test_user.id, content_id=content.id, status="inbox")
    )
    db_session.commit()

    assert is_visible_long_form_image_candidate(db_session, content) is True


def test_article_missing_list_ready_summary_is_not_eligible(db_session, test_user) -> None:
    content = Content(
        url="https://example.com/article-no-bullets",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "summary": {"title": "Bad article", "overview": "Missing bullets"},
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.add(
        ContentStatusEntry(user_id=test_user.id, content_id=content.id, status="inbox")
    )
    db_session.commit()

    assert is_visible_long_form_image_candidate(db_session, content) is False


def test_visible_podcast_with_provider_thumbnail_is_eligible_and_enqueues(
    db_session,
    test_user,
) -> None:
    content = Content(
        url="https://example.com/podcast",
        content_type=ContentType.PODCAST.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "summary": _build_podcast_summary("Visible podcast"),
            "summary_kind": "long_editorial_narrative",
            "summary_version": 1,
            "thumbnail_url": "https://img.youtube.com/example.png",
            "video_id": "abc123",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.add(
        ContentStatusEntry(user_id=test_user.id, content_id=content.id, status="inbox")
    )
    db_session.commit()

    queue = DummyQueue()
    task_id = enqueue_visible_long_form_image_if_needed(
        db_session,
        content,
        queue_service=queue,
    )

    assert task_id == 1
    assert queue.calls == [(TaskType.GENERATE_IMAGE, content.id)]


def test_enqueue_skips_when_active_generate_image_task_exists(db_session, test_user) -> None:
    content = Content(
        url="https://example.com/queued-article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "summary": _build_article_summary("Queued article"),
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.add_all(
        [
            ContentStatusEntry(user_id=test_user.id, content_id=content.id, status="inbox"),
            ProcessingTask(
                task_type=TaskType.GENERATE_IMAGE.value,
                content_id=content.id,
                status=TaskStatus.PENDING.value,
                queue_name="content",
            ),
        ]
    )
    db_session.commit()

    queue = DummyQueue()
    task_id = enqueue_visible_long_form_image_if_needed(
        db_session,
        content,
        queue_service=queue,
    )

    assert task_id is None
    assert queue.calls == []


def test_cancel_ineligible_pending_generate_image_tasks_preserves_visible_pending_task(
    db_session,
    test_user,
) -> None:
    eligible_content = Content(
        url="https://example.com/eligible",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "summary": _build_article_summary("Eligible"),
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )
    ineligible_content = Content(
        url="https://example.com/ineligible",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        classification="skip",
        content_metadata={
            "summary": _build_article_summary("Ineligible"),
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )
    db_session.add_all([eligible_content, ineligible_content])
    db_session.commit()
    db_session.add(
        ContentStatusEntry(user_id=test_user.id, content_id=eligible_content.id, status="inbox")
    )
    db_session.add_all(
        [
            ProcessingTask(
                task_type=TaskType.GENERATE_IMAGE.value,
                content_id=eligible_content.id,
                status=TaskStatus.PENDING.value,
                queue_name="content",
            ),
            ProcessingTask(
                task_type=TaskType.GENERATE_IMAGE.value,
                content_id=ineligible_content.id,
                status=TaskStatus.PENDING.value,
                queue_name="content",
            ),
            ProcessingTask(
                task_type=TaskType.GENERATE_IMAGE.value,
                content_id=ineligible_content.id,
                status=TaskStatus.PROCESSING.value,
                queue_name="content",
            ),
        ]
    )
    db_session.commit()

    cancelled_ids = cancel_ineligible_pending_generate_image_tasks(db_session)

    pending_tasks = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.GENERATE_IMAGE.value)
        .order_by(ProcessingTask.id.asc())
        .all()
    )

    assert cancelled_ids == [pending_tasks[1].id]
    assert pending_tasks[0].status == TaskStatus.PENDING.value
    assert pending_tasks[1].status == TaskStatus.FAILED.value
    assert pending_tasks[1].error_message == CANCELLED_NOT_VISIBLE_UNDER_FEED_RULES
    assert pending_tasks[2].status == TaskStatus.PROCESSING.value
