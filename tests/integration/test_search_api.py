"""Integration tests for content search endpoints."""

import pytest

from app.models.chat_message_metadata import AssistantFeedOption, AssistantFeedOptionsResult


@pytest.fixture
def search_seeded_content(content_factory, status_entry_factory, test_user):
    """Create visible search content covering article, podcast, and skipped rows."""
    items = [
        content_factory(
            content_type="article",
            url="https://example.com/ai-article",
            title="Understanding AI in 2025",
            source="Tech Blog",
            platform="substack",
            status="completed",
            content_metadata={
                "summary": {
                    "title": "Understanding AI in 2025",
                    "overview": (
                        "Deep dive into artificial intelligence and its evolution across "
                        "research, product, and policy landscapes in 2025."
                    ),
                    "bullet_points": [
                        {
                            "text": "AI systems are improving across multi-modal tasks.",
                            "category": "key_finding",
                        },
                        {
                            "text": "Deployment practices emphasize safety and monitoring.",
                            "category": "methodology",
                        },
                        {
                            "text": "Regulators are aligning on AI risk frameworks.",
                            "category": "context",
                        },
                    ],
                    "topics": ["AI", "Policy", "Product"],
                },
                "image_generated_at": "2025-01-01T00:00:00Z",
            },
        ),
        content_factory(
            content_type="podcast",
            url="https://example.com/podcast-ep1",
            title="Tech Talk Episode 1",
            source="Tech Podcast",
            platform="youtube",
            status="completed",
            content_metadata={
                "transcript": "Today we discuss machine learning and AI systems",
                "summary": {
                    "title": "Tech Talk Episode 1",
                    "overview": "Discussion about machine learning",
                },
            },
        ),
        content_factory(
            content_type="article",
            url="https://example.com/skip-me",
            title="Skip This",
            source="Misc",
            classification="skip",
            status="completed",
            content_metadata={"summary": {"title": "Skip This", "overview": "Not relevant"}},
        ),
    ]
    for item in items:
        status_entry_factory(user=test_user, content=item, status="inbox")
    return items


class TestSearchAPI:
    def test_search_basic(self, client, search_seeded_content) -> None:
        response = client.get("/api/content/search", params={"q": "AI"})
        assert response.status_code == 200

        payload = response.json()
        assert payload["meta"]["total"] >= 1
        for content in payload["contents"]:
            assert content["title"] != "Skip This"

    def test_search_type_filter(self, client, search_seeded_content) -> None:
        response = client.get("/api/content/search", params={"q": "tech", "type": "article"})
        assert response.status_code == 200

        for content in response.json()["contents"]:
            assert content["content_type"] == "article"

    def test_search_keeps_long_form_results_without_generated_artwork(
        self,
        client,
        content_factory,
        status_entry_factory,
        test_user,
    ) -> None:
        article = content_factory(
            content_type="article",
            url="https://example.com/no-art-search-article",
            title="Backend Search Visibility",
            source="Example Source",
            status="completed",
            content_metadata={
                "summary": {
                    "title": "Backend Search Visibility",
                    "overview": (
                        "This long-form article should stay searchable even before generated "
                        "artwork finishes, because search is not an inbox/feed surface."
                    ),
                    "bullet_points": [
                        {
                            "text": (
                                "Search should continue to return summary-ready long-form items."
                            ),
                            "category": "key_finding",
                        },
                        {
                            "text": (
                                "Artwork gating should remain limited to inbox/feed visibility."
                            ),
                            "category": "context",
                        },
                        {
                            "text": (
                                "History and library surfaces should not inherit feed-only gating."
                            ),
                            "category": "conclusion",
                        },
                    ],
                    "topics": ["Search", "Feeds"],
                },
                "summary_kind": "long_structured",
                "summary_version": 1,
            },
        )
        status_entry_factory(user=test_user, content=article, status="inbox")

        response = client.get("/api/content/search", params={"q": "Visibility"})
        assert response.status_code == 200

        titles = {content["title"] for content in response.json()["contents"]}
        assert "Backend Search Visibility" in titles

    def test_search_validation(self, client) -> None:
        response = client.get("/api/content/search", params={"q": "a"})
        assert response.status_code == 422

        response = client.get("/api/content/search", params={"q": "ai", "type": "video"})
        assert response.status_code == 422

    def test_mixed_search_returns_sectioned_results(
        self,
        client,
        search_seeded_content,
        monkeypatch,
    ) -> None:
        del search_seeded_content
        monkeypatch.setattr(
            "app.queries.search_mixed.find_feed_options",
            lambda query, limit: AssistantFeedOptionsResult(
                query=query,
                options=[
                    AssistantFeedOption(
                        id="feed-option-0001",
                        title="AI Weekly",
                        site_url="https://ai.example.com",
                        feed_url="https://ai.example.com/feed.xml",
                        feed_type="atom",
                        feed_format="rss",
                        description="AI coverage",
                        rationale="Validated feed",
                        evidence_url="https://ai.example.com",
                    )
                ],
            ),
        )
        monkeypatch.setattr(
            "app.queries.search_mixed.search_podcast_episodes",
            lambda query, limit: [
                type(
                    "PodcastHit",
                    (),
                    {
                        "title": "AI Weekly Episode",
                        "episode_url": "https://podcasts.example.com/episodes/1",
                        "podcast_title": "AI Weekly",
                        "source": "example.fm",
                        "snippet": "Episode summary",
                        "feed_url": "https://podcasts.example.com/feed.xml",
                        "published_at": "2026-02-19T00:00:00Z",
                        "provider": "listen_notes",
                        "score": 1.0,
                    },
                )()
            ],
        )

        response = client.get(
            "/api/content/search/mixed",
            params={"q": "Understanding", "limit": 5},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["query"] == "Understanding"
        assert payload["content"]
        assert payload["content"][0]["title"] == "Understanding AI in 2025"
        assert payload["feeds"][0]["feed_url"] == "https://ai.example.com/feed.xml"
        assert payload["podcasts"][0]["episode_url"] == "https://podcasts.example.com/episodes/1"
