"""Tests for mixed search query orchestration."""

from __future__ import annotations

from types import SimpleNamespace

from app.models.api.common import ContentSummaryResponse
from app.models.contracts import ContentClassification, ContentStatus, ContentType
from app.queries import search_mixed


def test_search_mixed_combines_local_feed_and_podcast_results(monkeypatch) -> None:
    calls: dict[str, object] = {}
    content_card = ContentSummaryResponse(
        id=1,
        content_type=ContentType.ARTICLE,
        url="https://example.com/article",
        title="Local Article",
        status=ContentStatus.COMPLETED,
        short_summary="Local result",
        created_at="2026-04-27T12:00:00Z",
        classification=ContentClassification.TO_READ,
    )

    def fake_search_content_cards_execute(db, **kwargs):
        calls["content"] = {"db": db, **kwargs}
        return SimpleNamespace(contents=[content_card])

    def fake_find_feed_options(**kwargs):
        calls["feeds"] = kwargs
        return SimpleNamespace(
            options=[
                SimpleNamespace(
                    id="feed-1",
                    title="Example Feed",
                    site_url="https://example.com",
                    feed_url="https://example.com/feed",
                    feed_type="substack",
                    feed_format="rss",
                    description="Feed description",
                    rationale="Good match",
                    evidence_url="https://example.com/about",
                )
            ]
        )

    def fake_search_podcast_episodes(**kwargs):
        calls["podcasts"] = kwargs
        return [
            SimpleNamespace(
                title="Episode",
                episode_url="https://podcasts.example.com/episode",
                podcast_title="Podcast",
                source="listen_notes",
                snippet="Snippet",
                feed_url="https://podcasts.example.com/feed",
                published_at="2026-04-26T12:00:00Z",
                provider="listen_notes",
                score=0.9,
            )
        ]

    monkeypatch.setattr(
        search_mixed.search_content_cards,
        "execute",
        fake_search_content_cards_execute,
    )
    monkeypatch.setattr(search_mixed, "find_feed_options", fake_find_feed_options)
    monkeypatch.setattr(search_mixed, "search_podcast_episodes", fake_search_podcast_episodes)

    db = object()
    response = search_mixed.execute(db, user_id=7, query="ai", limit=9)

    assert response.query == "ai"
    assert response.content == [content_card]
    assert response.feeds[0].feed_url == "https://example.com/feed"
    assert response.podcasts[0].episode_url == "https://podcasts.example.com/episode"
    assert calls["content"] == {
        "db": db,
        "user_id": 7,
        "q": "ai",
        "content_type": "all",
        "limit": 9,
        "cursor": None,
        "offset": 0,
    }
    assert calls["feeds"] == {"query": "ai", "limit": 5}
    assert calls["podcasts"] == {"query": "ai", "limit": 9}
