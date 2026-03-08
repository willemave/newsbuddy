from app.models.schema import Content, ContentStatusEntry


def test_submission_creates_content_status(client, db_session, test_user):
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
):
    enqueue_calls: list[tuple[str, int | None]] = []

    def _fake_enqueue(self, task_type, content_id=None, payload=None, queue_name=None, dedupe=None):
        _ = self, payload, queue_name, dedupe
        enqueue_calls.append((task_type.value, content_id))
        return 999

    monkeypatch.setattr("app.services.queue.QueueService.enqueue", _fake_enqueue)

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

    response = client.post(
        "/api/content/submit",
        json={"url": existing.url, "content_type": "article", "title": "Existing"},
    )
    assert response.status_code in (200, 201)
    assert ("generate_image", existing.id) in enqueue_calls
