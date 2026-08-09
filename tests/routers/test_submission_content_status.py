from app.models.contracts import TaskType
from app.models.db import Content, ContentStatusEntry, ProcessingTask
from app.services.queue import TaskEnqueueRequest


def test_submission_creates_content_status(client, db_session, test_user) -> None:
    payload = {
        "url": "https://example.com/article-1",
        "content_type": "article",
        "title": "Example",
    }
    resp = client.post("/api/content/submit", json=payload)
    assert resp.status_code in (200, 201)

    content = db_session.query(Content).filter_by(url=payload["url"]).first()
    assert content is not None

    status_row = (
        db_session.query(ContentStatusEntry)
        .filter_by(user_id=test_user.id, content_id=content.id)
        .first()
    )
    assert status_row is not None
    assert status_row.status == "inbox"


def test_submission_of_existing_visible_article_enqueues_generated_image(
    client,
    db_session,
    monkeypatch,
    test_user,
) -> None:
    existing = Content(
        url="https://example.com/visible-article",
        content_type="article",
        status="completed",
        content_metadata={
            "summary": {
                "title": "Visible article",
                "overview": (
                    "This overview is long enough to satisfy the minimum length "
                    "requirement for structured summaries."
                ),
                "bullet_points": [
                    {"text": "Key point one", "category": "key_finding"},
                    {"text": "Key point two", "category": "methodology"},
                    {"text": "Key point three", "category": "conclusion"},
                ],
                "quotes": [],
                "topics": ["Testing"],
            },
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )
    db_session.add(existing)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.content_submission.build_visible_long_form_image_task_requests",
        lambda _db, content_ids: [
            TaskEnqueueRequest(TaskType.GENERATE_IMAGE, content_id=content_ids[0])
        ],
    )

    response = client.post(
        "/api/content/submit",
        json={"url": existing.url, "content_type": "article", "title": "Existing"},
    )
    assert response.status_code in (200, 201)
    image_task = (
        db_session.query(ProcessingTask)
        .filter(
            ProcessingTask.task_type == TaskType.GENERATE_IMAGE.value,
            ProcessingTask.content_id == existing.id,
        )
        .one()
    )
    assert image_task.status == "pending"
