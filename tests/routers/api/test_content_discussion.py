"""Tests for content discussion API endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

from app.commands import refresh_content_discussion as refresh_discussion_command
from app.models.contracts import ContentStatus, ContentType
from app.models.db import ContentDiscussion, NewsItem, NewsItemDiscussion


def test_get_content_discussion_returns_not_ready_when_missing(
    client,
    content_factory,
    status_entry_factory,
    test_user,
) -> None:
    content = content_factory(
        content_type=ContentType.NEWS.value,
        url="https://example.com/story",
        title="Example",
        source="example.com",
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "platform": "hackernews",
            "discussion_url": "https://news.ycombinator.com/item?id=123",
        },
    )
    status_entry_factory(user=test_user, content=content, status="inbox")

    response = client.get(f"/api/content/{content.id}/discussion")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["mode"] == "none"
    assert payload["discussion_url"] == "https://news.ycombinator.com/item?id=123"


def test_get_content_discussion_returns_comments_payload(
    client,
    content_factory,
    db_session,
    discussion_payload_factory,
    status_entry_factory,
    test_user,
) -> None:
    content = content_factory(
        content_type=ContentType.NEWS.value,
        url="https://example.com/story",
        title="Example",
        source="example.com",
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "platform": "hackernews",
            "discussion_url": "https://news.ycombinator.com/item?id=123",
        },
    )
    status_entry_factory(user=test_user, content=content, status="inbox")
    db_session.add(
        ContentDiscussion(
            content_id=content.id,
            platform="hackernews",
            status="completed",
            discussion_data=discussion_payload_factory(
                discussion_url="https://news.ycombinator.com/item?id=123",
            ),
        )
    )
    db_session.commit()

    response = client.get(f"/api/content/{content.id}/discussion")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["mode"] == "comments"
    assert payload["comments"][0]["author"] == "alice"
    assert payload["links"][0]["url"] == "https://example.com"


def test_get_content_discussion_returns_discussion_list_payload(
    client,
    content_factory,
    db_session,
    discussion_payload_factory,
    status_entry_factory,
    test_user,
) -> None:
    content = content_factory(
        content_type=ContentType.NEWS.value,
        url="https://example.com/story",
        title="Example",
        source="example.com",
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "platform": "techmeme",
            "discussion_url": "https://www.techmeme.com/260217/p39#a260217p39",
        },
    )
    status_entry_factory(user=test_user, content=content, status="inbox")
    db_session.add(
        ContentDiscussion(
            content_id=content.id,
            platform="techmeme",
            status="completed",
            discussion_data=discussion_payload_factory(
                discussion_url="https://www.techmeme.com/260217/p39#a260217p39",
                mode="discussion_list",
            ),
        )
    )
    db_session.commit()

    response = client.get(f"/api/content/{content.id}/discussion")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "discussion_list"
    assert payload["discussion_groups"][0]["label"] == "Forums"
    assert payload["discussion_groups"][0]["items"][0]["title"] == "Hacker News"


def test_get_content_discussion_returns_404_when_missing_content(client) -> None:
    response = client.get("/api/content/999999/discussion")
    assert response.status_code == 404


def test_get_news_item_discussion_returns_comments_payload_from_legacy_content(
    client,
    content_factory,
    db_session,
    discussion_payload_factory,
    news_item_factory,
    test_user,
) -> None:
    content = content_factory(
        content_type=ContentType.NEWS.value,
        url="https://example.com/story",
        title="Example",
        source="example.com",
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "platform": "hackernews",
            "discussion_url": "https://news.ycombinator.com/item?id=456",
        },
    )
    news_item = news_item_factory(
        ingest_key="hn-456",
        platform="hackernews",
        canonical_item_url="https://example.com/story",
        discussion_url="https://news.ycombinator.com/item?id=456",
        raw_metadata={},
        status="ready",
        legacy_content_id=content.id,
    )
    db_session.add(
        ContentDiscussion(
            content_id=content.id,
            platform="hackernews",
            status="completed",
            discussion_data=discussion_payload_factory(
                discussion_url="https://news.ycombinator.com/item?id=456",
            ),
        )
    )
    db_session.commit()

    response = client.get(f"/api/news/items/{news_item.id}/discussion")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["mode"] == "comments"
    assert payload["discussion_url"] == "https://news.ycombinator.com/item?id=456"
    assert payload["comments"][0]["author"] == "alice"


def test_get_news_item_discussion_returns_embedded_payload_without_legacy_content(
    client,
    discussion_payload_factory,
    news_item_factory,
) -> None:
    news_item = news_item_factory(
        ingest_key="hn-embedded-discussion",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="hn-embedded-discussion",
        canonical_item_url="https://news.ycombinator.com/item?id=789",
        canonical_story_url="https://example.com/story",
        article_url="https://example.com/story",
        article_title="Embedded discussion story",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=789",
        summary_title="Embedded discussion story",
        summary_key_points=["Point one"],
        summary_text="Short summary",
        raw_metadata={
            "discussion_url": "https://news.ycombinator.com/item?id=789",
            "discussion_status": "completed",
            "discussion_fetched_at": "2026-04-04T12:00:00+00:00",
            "discussion_payload": discussion_payload_factory(
                discussion_url="https://news.ycombinator.com/item?id=789",
                comments=[
                    {
                        "comment_id": "c-embedded",
                        "author": "alice",
                        "text": "Embedded comment",
                        "compact_text": "Embedded comment",
                        "depth": 0,
                    }
                ],
                links=[{"url": "https://example.com/comment-link", "source": "comment"}],
                stats={"fetched_count": 1},
            ),
        },
        status="ready",
        published_at=datetime.now(UTC).replace(tzinfo=None),
        ingested_at=datetime.now(UTC).replace(tzinfo=None),
        processed_at=datetime.now(UTC).replace(tzinfo=None),
    )

    response = client.get(f"/api/news/items/{news_item.id}/discussion")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["mode"] == "comments"
    assert payload["discussion_url"] == "https://news.ycombinator.com/item?id=789"
    assert payload["fetched_at"] == "2026-04-04T12:00:00+00:00"
    assert payload["comments"][0]["author"] == "alice"
    assert payload["comments"][0]["text"] == "Embedded comment"
    assert payload["links"][0]["url"] == "https://example.com/comment-link"


def test_refresh_content_discussion_returns_refreshed_payload(
    client,
    content_factory,
    db_session,
    discussion_payload_factory,
    status_entry_factory,
    test_user,
    monkeypatch,
) -> None:
    content = content_factory(
        content_type=ContentType.NEWS.value,
        url="https://example.com/story",
        title="Example",
        source="example.com",
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "platform": "hackernews",
            "discussion_url": "https://news.ycombinator.com/item?id=123",
        },
    )
    status_entry_factory(user=test_user, content=content, status="inbox")

    def _refresh_and_store(db, *, content_id: int, comment_cap: int = 500):
        del comment_cap
        row = db.query(ContentDiscussion).filter(ContentDiscussion.content_id == content_id).first()
        if row is None:
            row = ContentDiscussion(content_id=content_id)
            db.add(row)
        row.platform = "hackernews"
        row.status = "completed"
        row.discussion_data = discussion_payload_factory(
            discussion_url="https://news.ycombinator.com/item?id=123",
            comments=[
                {
                    "comment_id": "c-refresh",
                    "author": "fresh-user",
                    "text": "Newest comment",
                    "compact_text": "Newest comment",
                    "depth": 0,
                }
            ],
            links=[],
            stats={"fetched_count": 1},
        )
        db.commit()

    monkeypatch.setattr(
        refresh_discussion_command,
        "fetch_and_store_discussion",
        _refresh_and_store,
    )

    response = client.post(f"/api/content/{content.id}/discussion/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["mode"] == "comments"
    assert payload["comments"][0]["comment_id"] == "c-refresh"
    assert payload["comments"][0]["author"] == "fresh-user"


def test_refresh_news_item_discussion_uses_news_item_discussion_summary_for_reddit(
    client,
    content_factory,
    db_session,
    news_item_factory,
    test_user,
    monkeypatch,
) -> None:
    content = content_factory(
        content_type=ContentType.NEWS.value,
        url="https://example.com/story",
        title="Example",
        source="example.com",
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "platform": "reddit",
            "discussion_url": "https://www.reddit.com/r/test/comments/abc123/thread/",
        },
    )
    news_item = news_item_factory(
        ingest_key="reddit-legacy-refresh",
        visibility_scope="user",
        owner_user_id=test_user.id,
        platform="reddit",
        source_type="reddit",
        source_label="reddit",
        canonical_item_url="https://www.reddit.com/r/test/comments/abc123/thread/",
        discussion_url="https://www.reddit.com/r/test/comments/abc123/thread/",
        legacy_content_id=content.id,
    )

    def _refresh_and_store(db, *, news_item_id: int, force: bool = False):
        del force
        assert news_item_id == news_item.id
        row = (
            db.query(NewsItemDiscussion)
            .filter(NewsItemDiscussion.news_item_id == news_item_id)
            .first()
        )
        if row is None:
            row = NewsItemDiscussion(news_item_id=news_item_id, platform="reddit")
            db.add(row)
        row.platform = "reddit"
        row.external_id = "abc123"
        row.discussion_url = "https://www.reddit.com/r/test/comments/abc123/thread/"
        row.comment_count = 3
        row.fetched_comment_count = 2
        row.summary_status = "completed"
        row.last_refresh_status = "completed"
        row.last_comments_fetched_at = datetime(2026, 4, 13, 12, 0, tzinfo=UTC).replace(tzinfo=None)
        row.summary = {
            "overview": "Reddit commenters focused on deployment risk and tradeoffs.",
            "topics": [
                {
                    "title": "Deployment risk",
                    "summary": "Several comments called out operational complexity.",
                }
            ],
            "notable_links": [
                {
                    "url": "https://example.com/deep-dive",
                    "title": "Deep dive",
                    "reason": "Adds context from the thread.",
                }
            ],
            "representative_comments": [],
            "external_discussion_url": "https://www.reddit.com/r/test/comments/abc123/thread/",
        }
        db.commit()

    monkeypatch.setattr(
        refresh_discussion_command.news_item_discussions,
        "refresh_news_item_discussion",
        _refresh_and_store,
    )

    response = client.post(f"/api/news/items/{news_item.id}/discussion/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["mode"] == "comments"
    assert payload["summary"]["overview"].startswith("Reddit commenters")
    assert payload["comment_count"] == 3
    assert payload["links"][0]["url"] == "https://example.com/deep-dive"


def test_refresh_news_item_discussion_updates_embedded_payload(
    client,
    discussion_payload_factory,
    news_item_factory,
    monkeypatch,
) -> None:
    news_item = news_item_factory(
        ingest_key="techmeme-embedded-refresh",
        platform="techmeme",
        source_type="techmeme",
        source_label="Techmeme",
        canonical_item_url="https://www.techmeme.com/260217/p39#a260217p39",
        discussion_url="https://www.techmeme.com/260217/p39#a260217p39",
        legacy_content_id=None,
    )

    def _refresh_and_store(db, *, news_item_id: int, comment_cap: int = 500):
        del comment_cap
        item = db.query(NewsItem).filter(NewsItem.id == news_item_id).first()
        assert item is not None
        metadata = dict(item.raw_metadata or {})
        metadata["discussion_status"] = "completed"
        metadata["discussion_fetched_at"] = "2026-04-13T12:00:00+00:00"
        metadata["discussion_payload"] = discussion_payload_factory(
            discussion_url="https://www.techmeme.com/260217/p39#a260217p39",
            mode="discussion_list",
            discussion_groups=[
                {
                    "label": "Forums",
                    "items": [
                        {
                            "title": "Hacker News",
                            "url": "https://news.ycombinator.com/item?id=123",
                        }
                    ],
                }
            ],
        )
        item.raw_metadata = metadata
        db.commit()

    monkeypatch.setattr(
        refresh_discussion_command,
        "fetch_and_store_news_item_discussion",
        _refresh_and_store,
    )

    response = client.post(f"/api/news/items/{news_item.id}/discussion/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["mode"] == "discussion_list"
    assert payload["fetched_at"] == "2026-04-13T12:00:00+00:00"
    assert payload["discussion_groups"][0]["items"][0]["title"] == "Hacker News"
