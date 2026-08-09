"""Social link detection and extraction tests for discussion fetcher."""

from __future__ import annotations

from typing import Any, cast

from app.services import discussion_fetcher
from app.services.discussion_fetcher import (
    _extract_social_comments_from_groups,
    _is_social_url,
)


class TestIsSocialUrl:
    def test_x_dot_com(self) -> None:
        assert _is_social_url("https://x.com/user/status/123") is True

    def test_twitter(self) -> None:
        assert _is_social_url("https://twitter.com/user/status/123") is True

    def test_hackernews(self) -> None:
        assert _is_social_url("https://news.ycombinator.com/item?id=123") is True

    def test_reddit(self) -> None:
        assert _is_social_url("https://www.reddit.com/r/test/comments/abc/") is True

    def test_threads(self) -> None:
        assert _is_social_url("https://www.threads.net/@user/post/abc") is True

    def test_bluesky(self) -> None:
        assert _is_social_url("https://bsky.app/profile/user/post/abc") is True

    def test_mastodon(self) -> None:
        assert _is_social_url("https://mastodon.social/@user/123") is True

    def test_linkedin(self) -> None:
        assert _is_social_url("https://www.linkedin.com/posts/user-abc") is True

    def test_non_social_domain(self) -> None:
        assert _is_social_url("https://example.com/article") is False

    def test_non_social_blog(self) -> None:
        assert _is_social_url("https://techcrunch.com/2025/01/story") is False


class TestExtractSocialCommentsFromGroups:
    def test_extracts_social_links_as_comments(self) -> None:
        groups = [
            {
                "label": "Discussion",
                "items": [
                    {"title": "HN Thread", "url": "https://news.ycombinator.com/item?id=123"},
                    {
                        "title": "Reddit discussion",
                        "url": "https://www.reddit.com/r/tech/comments/abc/thread/",
                    },
                ],
            }
        ]
        comments = _extract_social_comments_from_groups(groups)
        assert len(comments) == 2
        assert comments[0]["author"] == "news.ycombinator.com"
        assert comments[0]["text"] == "HN Thread"
        assert comments[0]["source_url"] == "https://news.ycombinator.com/item?id=123"
        assert comments[1]["author"] == "reddit.com"

    def test_skips_non_social_links(self) -> None:
        groups = [
            {
                "label": "More",
                "items": [
                    {"title": "TechCrunch", "url": "https://techcrunch.com/story"},
                    {"title": "X post", "url": "https://x.com/user/status/456"},
                ],
            }
        ]
        comments = _extract_social_comments_from_groups(groups)
        assert len(comments) == 1
        assert comments[0]["author"] == "x.com"

    def test_deduplicates_urls(self) -> None:
        groups = [
            {
                "label": "Forums",
                "items": [{"title": "HN", "url": "https://news.ycombinator.com/item?id=1"}],
            },
            {
                "label": "Social",
                "items": [
                    {
                        "title": "HN duplicate",
                        "url": "https://news.ycombinator.com/item?id=1",
                    }
                ],
            },
        ]
        comments = _extract_social_comments_from_groups(groups)
        assert len(comments) == 1

    def test_empty_groups(self) -> None:
        assert _extract_social_comments_from_groups([]) == []


def test_build_techmeme_payload_includes_social_comments(monkeypatch) -> None:
    groups = [
        {
            "label": "Forums",
            "items": [
                {"title": "Hacker News", "url": "https://news.ycombinator.com/item?id=123"},
                {"title": "TechCrunch Article", "url": "https://techcrunch.com/story"},
            ],
        }
    ]

    monkeypatch.setattr(
        discussion_fetcher,
        "_fetch_techmeme_discussion_groups",
        lambda *args, **kwargs: groups,
    )

    payload = discussion_fetcher._build_techmeme_payload(
        "https://www.techmeme.com/260217/p39#a260217p39",
        {"aggregator": {"external_id": "p39#a260217p39"}},
        http_service=cast(Any, object()),
    )

    assert payload.status == "completed"
    assert payload.mode == "discussion_list"
    assert payload.payload["mode"] == "discussion_list"
    comments = payload.payload["comments"]
    assert len(comments) == 1
    assert comments[0]["author"] == "news.ycombinator.com"
    assert comments[0]["text"] == "Hacker News"
    assert len(payload.payload["discussion_groups"]) == 1
