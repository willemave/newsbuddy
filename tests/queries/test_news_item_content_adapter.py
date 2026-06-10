"""Tests for adapting canonical news_items rows into content API cards."""

from __future__ import annotations

from datetime import datetime

from app.models.contracts import ContentStatus, ContentType, NewsItemStatus, NewsItemVisibilityScope
from app.models.db import NewsItem
from app.presenters.news_item_content_adapter import (
    present_news_item_detail,
    present_news_item_summary,
)
from app.services.news_relevant_links import NEWS_ARTICLE_RELEVANT_LINKS_KEY


def _news_item() -> NewsItem:
    return NewsItem(
        id=42,
        ingest_key="hn:42",
        visibility_scope=NewsItemVisibilityScope.GLOBAL.value,
        platform="hackernews",
        source_label="Hacker News",
        canonical_item_url="https://news.ycombinator.com/item?id=42",
        article_url="https://example.com/story",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=42",
        summary_key_points=["First point", "Second point"],
        summary_text="A compact summary of the story.",
        raw_metadata={
            "article": {"title": "Article title"},
            "summary": {"classification": "to_read", "title": "Summary title"},
            "top_comment": {"author": "alice", "text": "Useful context"},
            "comment_count": "12",
        },
        status=NewsItemStatus.READY.value,
        published_at=datetime(2026, 4, 25, 12, 30, 0),
        ingested_at=datetime(2026, 4, 25, 13, 0, 0),
        processed_at=datetime(2026, 4, 25, 13, 5, 0),
    )


def test_present_news_item_summary_maps_directly_to_content_card() -> None:
    response = present_news_item_summary(_news_item(), is_read=True)

    assert response.id == 42
    assert response.content_type == ContentType.NEWS
    assert response.status == ContentStatus.COMPLETED
    assert response.url == "https://example.com/story"
    assert response.source == "Hacker News"
    assert response.news_article_url == "https://example.com/story"
    assert response.news_discussion_url == "https://news.ycombinator.com/item?id=42"
    assert response.news_key_points == ["First point", "Second point"]
    assert response.news_summary == "A compact summary of the story."
    assert response.top_comment == {"author": "alice", "text": "Useful context"}
    assert response.comment_count == 12
    assert response.is_read is True


def test_present_news_item_summary_suppresses_techmeme_link_preview_comment() -> None:
    item = _news_item()
    item.platform = "techmeme"
    item.source_label = "Techmeme"
    item.discussion_url = "https://www.techmeme.com/260217/p39#a260217p39"
    item.raw_metadata = {
        "article": {"title": "Article title"},
        "summary": {"classification": "to_read", "title": "Summary title"},
        "top_comment": {"author": "x.com", "text": "@sama"},
        "comment_count": "8",
    }

    response = present_news_item_summary(item, is_read=False)

    assert response.top_comment is None
    assert response.comment_count == 8


def test_present_news_item_detail_builds_content_detail_without_content_metadata() -> None:
    response = present_news_item_detail(_news_item(), is_read=False)

    assert response.id == 42
    assert response.content_type == ContentType.NEWS
    assert response.title == "Summary title"
    assert response.display_title == "Summary title"
    assert response.body_available is False
    assert response.summary == "A compact summary of the story."
    assert response.news_key_points == ["First point", "Second point"]
    assert response.metadata["article"]["url"] == "https://example.com/story"
    assert response.metadata["article"]["title"] == "Article title"
    assert response.metadata["summary"]["article_url"] == "https://example.com/story"
    assert response.is_read is False


def test_present_news_item_detail_can_advertise_article_body() -> None:
    response = present_news_item_detail(
        _news_item(),
        is_read=False,
        body_available=True,
        body_format="text",
    )

    assert response.body_available is True
    assert response.body_kind == "article"
    assert response.body_format == "text"


def test_present_news_item_detail_exposes_source_metadata_without_body_ref() -> None:
    item = _news_item()
    assert isinstance(item.raw_metadata, dict)
    item.raw_metadata = {
        **item.raw_metadata,
        "article_body_ref": {
            "kind": "storage",
            "storage_key": "content-bodies/news-items/42/source.txt",
        },
        "source_metadata": {
            "schema_version": 1,
            "kind": "research_paper",
            "provider": "arxiv",
            "source_id": "2509.15194v2",
            "brief_synopsis": "A brief synopsis.",
        },
    }

    response = present_news_item_detail(item, is_read=False)

    assert "article_body_ref" not in response.metadata
    assert response.metadata["source_metadata"]["source_id"] == "2509.15194v2"


def test_present_news_item_detail_projects_relevant_links_without_related_links() -> None:
    item = _news_item()
    item.raw_metadata = {
        "article": {"title": "Article title"},
        "summary": {"classification": "to_read", "title": "Summary title"},
        NEWS_ARTICLE_RELEVANT_LINKS_KEY: [
            {
                "url": "https://docs.example.com/api",
                "title": "API docs",
                "reason": "Explains the API surface.",
            }
        ],
        "aggregator": {
            "metadata": {
                "related_links": [
                    {"url": "https://related.example.com/story", "title": "Related story"}
                ]
            }
        },
    }

    response = present_news_item_detail(
        item,
        is_read=False,
        discussion_summary={
            "notable_links": [
                {
                    "url": "https://github.com/example/project",
                    "title": "Project repo",
                    "reason": "Commenters pointed to the implementation.",
                }
            ]
        },
    )

    assert response.metadata["relevant_links"] == [
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
