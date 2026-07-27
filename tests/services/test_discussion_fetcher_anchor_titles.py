"""Anchor title and link extraction tests for discussion fetcher."""

from __future__ import annotations

from app.services import discussion_fetcher
from app.services.discussion_comments import (
    extract_anchor_titles_from_html,
    extract_links_from_comments,
    is_url_like_text,
)
from tests.services._discussion_fetcher_helpers import (
    FakeComment,
    FakeRedditClient,
    FakeSubmission,
)


class TestExtractAnchorTitlesFromHtml:
    def test_extracts_meaningful_anchor_text(self) -> None:
        html = '<a href="https://example.com/article">Great Article</a>'
        result = extract_anchor_titles_from_html(html)
        assert result["https://example.com/article"] == "Great Article"

    def test_skips_trivial_anchor_text(self) -> None:
        html = (
            '<a href="https://example.com/a">here</a> '
            '<a href="https://example.com/b">click here</a> '
            '<a href="https://example.com/c">link</a>'
        )
        result = extract_anchor_titles_from_html(html)
        assert result == {}

    def test_skips_url_like_anchor_text(self) -> None:
        html = '<a href="https://example.com/page">https://example.com/page</a>'
        result = extract_anchor_titles_from_html(html)
        assert result == {}

    def test_skips_url_without_scheme(self) -> None:
        html = '<a href="https://example.com/page">example.com/page</a>'
        result = extract_anchor_titles_from_html(html)
        assert result == {}

    def test_empty_input(self) -> None:
        assert extract_anchor_titles_from_html("") == {}
        assert extract_anchor_titles_from_html("plain text, no anchors") == {}

    def test_multiple_links_first_wins(self) -> None:
        html = (
            '<a href="https://example.com">First Title</a> '
            '<a href="https://example.com">Second Title</a>'
        )
        result = extract_anchor_titles_from_html(html)
        assert result["https://example.com"] == "First Title"

    def test_mixed_trivial_and_meaningful(self) -> None:
        html = (
            '<a href="https://example.com/a">here</a> '
            '<a href="https://example.com/b">Interesting Paper</a>'
        )
        result = extract_anchor_titles_from_html(html)
        assert "https://example.com/a" not in result
        assert result["https://example.com/b"] == "Interesting Paper"


class TestIsUrlLikeText:
    def test_exact_match(self) -> None:
        assert is_url_like_text("https://example.com", "https://example.com") is True

    def test_without_scheme(self) -> None:
        assert is_url_like_text("example.com/page", "https://example.com/page") is True

    def test_without_www(self) -> None:
        assert is_url_like_text("example.com/page", "https://www.example.com/page") is True

    def test_meaningful_text(self) -> None:
        assert is_url_like_text("Great Article", "https://example.com") is False

    def test_trailing_slash_ignored(self) -> None:
        assert is_url_like_text("https://example.com/", "https://example.com") is True


class TestExtractLinksFromCommentsWithTitles:
    def test_includes_title_when_available(self) -> None:
        comments = [{"comment_id": "c1", "text": "Check out https://example.com/article"}]
        url_titles = {"https://example.com/article": "Detailed Analysis"}
        links = extract_links_from_comments(comments, url_titles=url_titles)
        assert len(links) == 1
        assert links[0]["title"] == "Detailed Analysis"
        assert links[0]["url"] == "https://example.com/article"

    def test_omits_title_when_not_available(self) -> None:
        comments = [{"comment_id": "c1", "text": "See https://example.com/other"}]
        url_titles = {"https://example.com/article": "Detailed Analysis"}
        links = extract_links_from_comments(comments, url_titles=url_titles)
        assert len(links) == 1
        assert "title" not in links[0]

    def test_backward_compatible_without_url_titles(self) -> None:
        comments = [{"comment_id": "c1", "text": "Visit https://example.com/page"}]
        links = extract_links_from_comments(comments)
        assert len(links) == 1
        assert "title" not in links[0]


class TestRedditPipelineAnchorTitles:
    def test_body_html_titles_flow_through(self, monkeypatch) -> None:
        root = FakeComment(
            comment_id="c1",
            body="Check https://example.com/paper for the full paper",
            body_html=(
                'Check <a href="https://example.com/paper">Scaling Laws for LLMs</a>'
                " for the full paper"
            ),
            author="alice",
            created_utc=1_700_000_000,
        )
        fake_submission = FakeSubmission(title="Thread", num_comments=1, comments=[root])
        fake_client = FakeRedditClient(fake_submission)
        monkeypatch.setattr(discussion_fetcher, "_get_reddit_client", lambda: fake_client)

        payload = discussion_fetcher._build_reddit_payload(
            "https://reddit.com/r/test/comments/abc123/thread/",
            comment_cap=10,
        )

        links = payload.payload["links"]
        assert len(links) == 1
        assert links[0]["url"] == "https://example.com/paper"
        assert links[0]["title"] == "Scaling Laws for LLMs"

    def test_no_body_html_still_works(self, monkeypatch) -> None:
        root = FakeComment(
            comment_id="c1",
            body="Check https://example.com/page",
            author="alice",
            created_utc=1_700_000_000,
        )
        fake_submission = FakeSubmission(title="Thread", num_comments=1, comments=[root])
        fake_client = FakeRedditClient(fake_submission)
        monkeypatch.setattr(discussion_fetcher, "_get_reddit_client", lambda: fake_client)

        payload = discussion_fetcher._build_reddit_payload(
            "https://reddit.com/r/test/comments/abc123/thread/",
            comment_cap=10,
        )

        links = payload.payload["links"]
        assert len(links) == 1
        assert "title" not in links[0]
