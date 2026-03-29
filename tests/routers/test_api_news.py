"""Router tests for the news-native API."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.schema import NewsDigest, NewsDigestBullet, NewsDigestBulletSource, NewsItem


class _FakeQueueService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def enqueue(self, task_type, *, content_id=None, payload=None, queue_name=None, dedupe=None):  # noqa: ANN001
        self.calls.append((task_type.value, content_id))
        return len(self.calls)


def _create_news_fixture(db_session, user_id: int) -> tuple[NewsDigest, NewsDigestBullet, NewsItem]:
    news_item = NewsItem(
        ingest_key="fixture-item",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="fixture-item",
        canonical_item_url="https://news.ycombinator.com/item?id=1",
        canonical_story_url="https://example.com/story",
        article_url="https://example.com/story",
        article_title="Fixture story",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=1",
        summary_title="Fixture story",
        summary_key_points=["Fixture point"],
        summary_text="Fixture summary",
        raw_metadata={"discussion_payload": {"compact_comments": ["Useful comment"]}},
        status="ready",
        ingested_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(news_item)
    db_session.flush()

    digest = NewsDigest(
        user_id=user_id,
        timezone="UTC",
        window_start_at=datetime.now(UTC).replace(tzinfo=None),
        window_end_at=datetime.now(UTC).replace(tzinfo=None),
        title="Fixture digest",
        summary="Fixture digest summary",
        source_count=1,
        group_count=1,
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        llm_model="google:gemini-3.1-flash-lite-preview",
        pipeline_version="news-native-v1",
        trigger_reason="manual_test",
        generated_at=datetime.now(UTC).replace(tzinfo=None),
        build_metadata={},
    )
    db_session.add(digest)
    db_session.flush()

    bullet = NewsDigestBullet(
        digest_id=digest.id,
        position=1,
        topic="Fixture topic",
        details="Fixture details.",
        source_count=1,
    )
    db_session.add(bullet)
    db_session.flush()

    db_session.add(
        NewsDigestBulletSource(
            bullet_id=bullet.id,
            news_item_id=news_item.id,
            position=1,
        )
    )
    db_session.commit()
    return digest, bullet, news_item


def test_list_and_mark_read_news_digests(client, db_session, test_user) -> None:
    digest, _bullet, _news_item = _create_news_fixture(db_session, test_user.id)

    response = client.get("/api/news/digests")
    assert response.status_code == 200
    payload = response.json()
    assert payload["digests"][0]["id"] == digest.id
    assert payload["digests"][0]["bullets"][0]["topic"] == "Fixture topic"

    mark_read_response = client.post(f"/api/news/digests/{digest.id}/mark-read")
    assert mark_read_response.status_code == 200
    assert mark_read_response.json()["is_read"] is True


def test_get_news_item_and_convert_to_article(client, db_session, test_user, monkeypatch) -> None:
    _digest, _bullet, news_item = _create_news_fixture(db_session, test_user.id)
    fake_queue = _FakeQueueService()
    monkeypatch.setattr("app.routers.api.news.get_queue_service", lambda: fake_queue)

    item_response = client.get(f"/api/news/items/{news_item.id}")
    assert item_response.status_code == 200
    assert item_response.json()["summary_title"] == "Fixture story"

    convert_response = client.post(f"/api/news/items/{news_item.id}/convert-to-article")
    assert convert_response.status_code == 200
    assert convert_response.json()["already_exists"] is False
    assert fake_queue.calls == [("process_content", convert_response.json()["new_content_id"])]


def test_start_news_digest_bullet_dig_deeper(client, db_session, test_user, monkeypatch) -> None:
    digest, bullet, _news_item = _create_news_fixture(db_session, test_user.id)
    monkeypatch.setattr("app.routers.api.news.process_message_async", lambda *args, **kwargs: None)

    response = client.post(f"/api/news/digests/{digest.id}/bullets/{bullet.id}/dig-deeper")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["topic"] == "Fixture topic"
    assert payload["status"] == "processing"
