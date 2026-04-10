"""Router tests for the news-item feed API."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content, NewsItem, NewsItemReadStatus
from app.repositories import knowledge_repository


class _FakeQueueService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def enqueue(  # noqa: ANN001
        self,
        task_type,
        *,
        content_id=None,
        payload=None,
        queue_name=None,
        dedupe=None,
    ) -> int:
        del payload, queue_name, dedupe
        self.calls.append((task_type.value, content_id))
        return len(self.calls)


def _create_news_item(
    db_session,
    *,
    ingest_key: str,
    summary_title: str,
    article_title: str | None = None,
    summary_text: str | None = None,
    is_representative: bool = True,
    representative_news_item_id: int | None = None,
    raw_metadata: dict | None = None,
    published_at: datetime | None = None,
    ingested_at: datetime | None = None,
) -> NewsItem:
    metadata = {
        "cluster": {
            "member_ids": [ingest_key],
            "source_labels": ["Hacker News"],
            "domains": ["example.com"],
            "discussion_snippets": ["Useful comment"],
            "related_titles": [summary_title],
            "latest_member_ingested_at": datetime.now(UTC).isoformat(),
        }
    }
    if raw_metadata:
        metadata.update(raw_metadata)

    item = NewsItem(
        ingest_key=ingest_key,
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id=ingest_key,
        canonical_item_url=f"https://news.ycombinator.com/item?id={ingest_key}",
        canonical_story_url=f"https://example.com/{ingest_key}",
        article_url=f"https://example.com/{ingest_key}",
        article_title=article_title or summary_title,
        article_domain="example.com",
        discussion_url=f"https://news.ycombinator.com/item?id={ingest_key}",
        summary_title=summary_title,
        summary_key_points=["Point one", "Point two"],
        summary_text=summary_text or f"{summary_title} summary",
        raw_metadata=metadata,
        representative_news_item_id=None if is_representative else representative_news_item_id,
        cluster_size=2 if is_representative else 1,
        status="ready",
        published_at=published_at.replace(tzinfo=None) if published_at else None,
        ingested_at=(ingested_at or datetime.now(UTC)).replace(tzinfo=None),
        processed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(item)
    db_session.flush()
    return item


def test_list_news_items_hides_suppressed_members_and_marks_read(
    client,
    db_session,
    test_user,
) -> None:
    representative = _create_news_item(
        db_session,
        ingest_key="rep-1",
        summary_title="Representative story",
    )
    _create_news_item(
        db_session,
        ingest_key="dup-1",
        summary_title="Duplicate story",
        is_representative=False,
        representative_news_item_id=representative.id,
    )
    db_session.commit()

    response = client.get("/api/news/items", params={"read_filter": "unread"})
    assert response.status_code == 200

    payload = response.json()
    assert [item["id"] for item in payload["contents"]] == [representative.id]
    assert payload["contents"][0]["title"] == "Representative story"
    assert payload["contents"][0]["comment_count"] == 1
    assert payload["content_types"] == ["news"]

    mark_read_response = client.post(
        "/api/news/items/mark-read",
        json={"content_ids": [representative.id]},
    )
    assert mark_read_response.status_code == 200
    assert mark_read_response.json() == {
        "status": "success",
        "marked_count": 1,
        "failed_ids": [],
        "total_requested": 1,
    }

    db_session.refresh(representative)
    read_status = (
        db_session.query(NewsItemReadStatus)
        .filter(
            NewsItemReadStatus.user_id == test_user.id,
            NewsItemReadStatus.news_item_id == representative.id,
        )
        .one_or_none()
    )
    assert read_status is not None

    unread_response = client.get("/api/news/items", params={"read_filter": "unread"})
    assert unread_response.status_code == 200
    assert unread_response.json()["contents"] == []

    read_response = client.get("/api/news/items", params={"read_filter": "read"})
    assert read_response.status_code == 200
    assert [item["id"] for item in read_response.json()["contents"]] == [representative.id]


def test_list_news_items_falls_back_from_blocked_titles_to_summary_text(
    client,
    db_session,
) -> None:
    item = _create_news_item(
        db_session,
        ingest_key="blocked-title",
        summary_title="Subscribe to read",
        article_title="wsj.com",
        summary_text="OpenAI and Oracle discuss a new enterprise infrastructure partnership.",
    )
    db_session.commit()

    response = client.get("/api/news/items")

    assert response.status_code == 200
    payload = response.json()
    listed = next(content for content in payload["contents"] if content["id"] == item.id)
    assert (
        listed["title"]
        == "OpenAI and Oracle discuss a new enterprise infrastructure partnership."
    )


def test_mark_news_items_read_is_idempotent_for_existing_rows(
    client,
    db_session,
    test_user,
) -> None:
    representative = _create_news_item(
        db_session,
        ingest_key="rep-existing",
        summary_title="Representative story",
    )
    db_session.add(
        NewsItemReadStatus(
            user_id=test_user.id,
            news_item_id=representative.id,
            read_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    db_session.commit()

    response = client.post(
        "/api/news/items/mark-read",
        json={"content_ids": [representative.id]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "marked_count": 0,
        "failed_ids": [],
        "total_requested": 1,
    }
    assert (
        db_session.query(NewsItemReadStatus)
        .filter(
            NewsItemReadStatus.user_id == test_user.id,
            NewsItemReadStatus.news_item_id == representative.id,
        )
        .count()
        == 1
    )


def test_list_news_items_uses_denormalized_comment_count_when_available(
    client,
    db_session,
) -> None:
    news_item = _create_news_item(
        db_session,
        ingest_key="rep-comments",
        summary_title="Representative story",
        raw_metadata={
            "comment_count": 42,
            "aggregator": {"metadata": {"comments_count": 17}},
        },
    )
    db_session.commit()

    response = client.get("/api/news/items")
    assert response.status_code == 200

    payload = response.json()
    assert [item["id"] for item in payload["contents"]] == [news_item.id]
    assert payload["contents"][0]["comment_count"] == 42


def test_get_news_item_detail_includes_cluster_metadata(client, db_session) -> None:
    news_item = _create_news_item(
        db_session,
        ingest_key="detail-1",
        summary_title="Detail story",
    )
    db_session.commit()

    response = client.get(f"/api/news/items/{news_item.id}")
    assert response.status_code == 200

    payload = response.json()
    assert payload["id"] == news_item.id
    assert payload["display_title"] == "Detail story"
    assert payload["metadata"]["cluster"]["related_titles"] == ["Detail story"]
    assert payload["metadata"]["summary"]["key_points"] == ["Point one", "Point two"]


def test_list_news_items_falls_back_from_placeholder_titles(client, db_session) -> None:
    news_item = _create_news_item(
        db_session,
        ingest_key="skill-0",
        summary_title="SKILL0",
        article_title="SKILL0",
        summary_text=(
            "A Hugging Face space demo that explains how a tiny skill model works in practice."
        ),
    )
    db_session.commit()

    response = client.get("/api/news/items")
    assert response.status_code == 200

    payload = response.json()
    expected_title = (
        "A Hugging Face space demo that explains how a tiny skill model works in practice."
    )
    assert payload["contents"][0]["title"] == expected_title
    assert payload["contents"][0]["id"] == news_item.id


def test_get_news_item_detail_restores_key_points_when_summary_metadata_is_empty(
    client,
    db_session,
) -> None:
    news_item = _create_news_item(
        db_session,
        ingest_key="detail-empty-summary",
        summary_title="Detail story",
        raw_metadata={
            "summary": {
                "summary": "Summary from metadata",
                "key_points": [],
            }
        },
    )
    db_session.commit()

    response = client.get(f"/api/news/items/{news_item.id}")
    assert response.status_code == 200

    payload = response.json()
    assert payload["metadata"]["summary"]["key_points"] == ["Point one", "Point two"]


def test_list_news_items_orders_by_published_at_before_ingested_at(
    client,
    db_session,
) -> None:
    older_published = _create_news_item(
        db_session,
        ingest_key="older-published",
        summary_title="Older published",
        published_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
        ingested_at=datetime(2026, 4, 2, 10, 0, tzinfo=UTC),
    )
    newer_published = _create_news_item(
        db_session,
        ingest_key="newer-published",
        summary_title="Newer published",
        published_at=datetime(2026, 4, 2, 9, 0, tzinfo=UTC),
        ingested_at=datetime(2026, 4, 1, 9, 0, tzinfo=UTC),
    )
    db_session.commit()

    response = client.get("/api/news/items", params={"limit": 10})
    assert response.status_code == 200

    payload = response.json()
    returned_ids = [item["id"] for item in payload["contents"]]
    assert returned_ids[:2] == [newer_published.id, older_published.id]


def test_convert_news_item_to_article_queues_processing(
    client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    news_item = _create_news_item(
        db_session,
        ingest_key="convert-1",
        summary_title="Convert me",
    )
    db_session.commit()

    fake_queue = _FakeQueueService()
    monkeypatch.setattr(
        "app.commands.convert_news_to_article.get_queue_service",
        lambda: fake_queue,
    )

    response = client.post(f"/api/news/items/{news_item.id}/convert-to-article")
    assert response.status_code == 200

    payload = response.json()
    assert payload["already_exists"] is False
    assert payload["news_item_id"] == news_item.id
    assert fake_queue.calls == [("process_content", payload["new_content_id"])]
    assert (
        knowledge_repository.is_saved_to_knowledge(
            db_session,
            payload["new_content_id"],
            test_user.id,
        )
        is True
    )

    article = db_session.query(Content).filter(Content.id == payload["new_content_id"]).one()
    assert article.url == "https://example.com/convert-1"


def test_convert_news_item_to_article_favorites_existing_article(
    client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    existing_article = Content(
        url="https://example.com/convert-existing",
        source_url="https://example.com/convert-existing",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Existing article",
        source="example.com",
    )
    db_session.add(existing_article)
    db_session.flush()

    news_item = _create_news_item(
        db_session,
        ingest_key="convert-existing",
        summary_title="Convert existing",
    )
    db_session.commit()

    fake_queue = _FakeQueueService()
    monkeypatch.setattr(
        "app.commands.convert_news_to_article.get_queue_service",
        lambda: fake_queue,
    )

    response = client.post(f"/api/news/items/{news_item.id}/convert-to-article")
    assert response.status_code == 200

    payload = response.json()
    assert payload["already_exists"] is True
    assert payload["new_content_id"] == existing_article.id
    assert fake_queue.calls == []
    assert (
        knowledge_repository.is_saved_to_knowledge(
            db_session,
            existing_article.id,
            test_user.id,
        )
        is True
    )


def test_removed_digest_routes_return_not_found(client) -> None:
    for path in (
        "/api/news/digests",
        "/api/news/digests/1",
        "/api/news/digests/1/mark-read",
        "/api/news/digests/1/bullets/1/dig-deeper",
        "/api/agent/digests",
    ):
        response = client.get(path) if path == "/api/news/digests" else client.post(path)
        assert response.status_code == 404
