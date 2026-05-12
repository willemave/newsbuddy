"""Tests for short-form news relevant-link projection."""

from app.services.news_relevant_links import (
    NEWS_ARTICLE_RELEVANT_LINKS_KEY,
    build_news_relevant_links,
)


def test_build_news_relevant_links_merges_article_and_community_links() -> None:
    links = build_news_relevant_links(
        {
            NEWS_ARTICLE_RELEVANT_LINKS_KEY: [
                {
                    "url": "https://example.com/story",
                    "title": "Primary article",
                    "reason": "Should be excluded.",
                },
                {
                    "url": "https://docs.example.com/api",
                    "title": "API docs",
                    "reason": "Explains the API surface.",
                },
            ],
            "aggregator": {
                "metadata": {
                    "related_links": [
                        {"url": "https://related.example.com", "title": "Related coverage"}
                    ]
                }
            },
        },
        article_url="https://example.com/story",
        discussion_url="https://news.ycombinator.com/item?id=42",
        discussion_summary={
            "notable_links": [
                {
                    "url": "https://docs.example.com/api",
                    "title": "Duplicate docs",
                    "reason": "Duplicate should lose to the article source.",
                },
                {
                    "url": "https://news.ycombinator.com/item?id=42",
                    "title": "HN",
                    "reason": "Should be excluded.",
                },
                {
                    "url": "https://github.com/example/project",
                    "title": "Project repo",
                    "reason": "Commenters pointed to the implementation.",
                },
            ]
        },
    )

    assert links == [
        {
            "url": "https://docs.example.com/api",
            "title": "API docs",
            "reason": "Explains the API surface.",
            "source": "article",
        },
        {
            "url": "https://github.com/example/project",
            "title": "Project repo",
            "reason": "Commenters pointed to the implementation.",
            "source": "community",
        },
    ]
