"""Tests for HackerNews processing strategy."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.hackernews_strategy import HackerNewsProcessorStrategy


@pytest.fixture
def mock_http_client():
    """Create a mock HTTP client."""
    return MagicMock(spec=RobustHttpClient)


@pytest.fixture
def hn_strategy(mock_http_client):
    """Create a HackerNews strategy instance."""
    return HackerNewsProcessorStrategy(mock_http_client)


class TestHackerNewsProcessorStrategy:
    """Test cases for HackerNews processor strategy."""

    def test_can_handle_hn_item_url(self, hn_strategy):
        """Test that strategy recognizes HN item URLs."""
        valid_urls = [
            "https://news.ycombinator.com/item?id=12345",
            "http://news.ycombinator.com/item?id=67890",
            "https://hacker-news.firebaseio.com/v0/item/12345",
        ]

        for url in valid_urls:
            assert hn_strategy.can_handle_url(url) is True

    def test_cannot_handle_non_hn_urls(self, hn_strategy):
        """Test that strategy rejects non-HN URLs."""
        invalid_urls = [
            "https://example.com/article",
            "https://news.ycombinator.com/news",
            "https://news.ycombinator.com/user?id=test",
            "https://github.com/project",
        ]

        for url in invalid_urls:
            assert hn_strategy.can_handle_url(url) is False

    def test_extract_item_id(self, hn_strategy):
        """Test item ID extraction from URLs."""
        test_cases = [
            ("https://news.ycombinator.com/item?id=12345", "12345"),
            ("https://hacker-news.firebaseio.com/v0/item/67890", "67890"),
            ("https://news.ycombinator.com/item?id=99999", "99999"),
        ]

        for url, expected_id in test_cases:
            assert hn_strategy._extract_item_id(url) == expected_id

    def test_extract_item_id_invalid_url(self, hn_strategy):
        """Test item ID extraction returns None for invalid URLs."""
        invalid_urls = [
            "https://news.ycombinator.com/news",
            "https://example.com",
            "not-a-url",
        ]

        for url in invalid_urls:
            assert hn_strategy._extract_item_id(url) is None

    @pytest.mark.asyncio
    async def test_fetch_item_data(self, hn_strategy):
        """Test fetching item data from HN API."""
        mock_response = {
            "id": 12345,
            "type": "story",
            "title": "Test Article",
            "by": "testuser",
            "score": 100,
            "descendants": 50,
            "time": 1234567890,
            "url": "https://example.com/article",
            "kids": [1, 2, 3],
        }

        # Patch the specific httpx.AsyncClient in the module
        with patch(
            "app.processing_strategies.hackernews_strategy.httpx.AsyncClient"
        ) as mock_client:
            mock_async_client = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_async_client

            # Create a mock response object
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()

            mock_async_client.get.return_value = mock_resp

            result = await hn_strategy._fetch_item_data("12345")

            assert result == mock_response
            mock_async_client.get.assert_called_once_with(
                "https://hacker-news.firebaseio.com/v0/item/12345.json", timeout=30.0
            )

    @pytest.mark.asyncio
    async def test_fetch_comments(self, hn_strategy):
        """Test fetching comments for an item."""
        mock_item = {
            "kids": [1001, 1002, 1003],
        }

        with patch.object(hn_strategy, "_fetch_comment", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [
                {
                    "id": 1001,
                    "author": "user1",
                    "text": "First comment",
                    "time": 1234567891,
                    "kids": [],
                    "depth": 0,
                },
                {
                    "id": 1002,
                    "author": "user2",
                    "text": "Second comment",
                    "time": 1234567892,
                    "kids": [],
                    "depth": 0,
                },
                None,  # Third comment returns None
            ]

            comments = await hn_strategy._fetch_comments(mock_item)

            assert len(comments) == 2
            assert comments[0]["author"] == "user1"
            assert comments[1]["author"] == "user2"

    @pytest.mark.asyncio
    async def test_fetch_comments_reuses_one_async_client(self, hn_strategy):
        """Comment fan-out should share one HTTP client per batch."""
        mock_item = {
            "kids": [1001, 1002, 1003],
        }

        with (
            patch("app.processing_strategies.hackernews_strategy.httpx.AsyncClient") as mock_client,
            patch.object(hn_strategy, "_fetch_comment", new_callable=AsyncMock) as mock_fetch,
        ):
            mock_async_client = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_async_client
            mock_fetch.side_effect = [
                {"id": 1001, "author": "user1", "text": "First comment"},
                {"id": 1002, "author": "user2", "text": "Second comment"},
                None,
            ]

            comments = await hn_strategy._fetch_comments(mock_item)

            assert len(comments) == 2
            assert mock_client.call_count == 1
            assert mock_fetch.call_count == 3

    def test_clean_html_text(self, hn_strategy):
        """Test HTML cleaning from HN comments."""
        test_cases = [
            ("<p>Hello world</p>", "Hello world"),
            ("Plain text", "Plain text"),
            (
                "<p>First paragraph</p><p>Second paragraph</p>",
                "First paragraph\n\nSecond paragraph",
            ),
            ("Text with &lt;code&gt; and &amp; symbols", "Text with <code> and & symbols"),
            ('<a href="http://example.com">Link</a>', "Link"),
        ]

        for html_input, expected_output in test_cases:
            assert hn_strategy._clean_html_text(html_input) == expected_output

    def test_format_comments_for_summary(self, hn_strategy):
        """Test formatting comments for LLM summarization."""
        comments = [
            {"author": "user1", "text": "This is the first comment"},
            {"author": "user2", "text": "This is the second comment"},
        ]

        result = hn_strategy._format_comments_for_summary(comments)

        assert "Comment 1 by user1:" in result
        assert "This is the first comment" in result
        assert "Comment 2 by user2:" in result
        assert "This is the second comment" in result
        assert "---" in result  # separator

    def test_format_comments_empty(self, hn_strategy):
        """Test formatting when no comments available."""
        result = hn_strategy._format_comments_for_summary([])
        assert result == "No comments available."

    def test_extract_data_text_post(self, hn_strategy):
        """Test extracting data from an Ask HN or text post."""
        url = "https://news.ycombinator.com/item?id=12345"

        mock_item_data = {
            "id": 12345,
            "type": "story",
            "title": "Ask HN: How do you manage technical debt?",
            "by": "askuser",
            "score": 150,
            "descendants": 75,
            "time": 1234567890,
            "text": "<p>I'm curious about how different teams handle technical debt...</p>",
            "kids": [1001, 1002],
        }

        with (
            patch.object(
                hn_strategy, "_fetch_item_data", new_callable=AsyncMock
            ) as mock_fetch_item,
            patch.object(
                hn_strategy, "_fetch_comments", new_callable=AsyncMock
            ) as mock_fetch_comments,
        ):
            mock_fetch_item.return_value = mock_item_data
            mock_fetch_comments.return_value = []

            result = hn_strategy.extract_data("", url)

            assert result["title"] == "Ask HN: How do you manage technical debt?"
            assert result["author"] == "askuser"
            assert result["hn_score"] == 150
            assert result["hn_comments_count"] == 75
            assert result["is_hn_text_post"] is True
            assert result["requires_content_fetch"] is False
            assert "curious about how different teams" in result["text_content"]

    def test_extract_data_link_post(self, hn_strategy):
        """Test extracting data from a link post."""
        url = "https://news.ycombinator.com/item?id=67890"

        mock_item_data = {
            "id": 67890,
            "type": "story",
            "title": "New AI breakthrough announced",
            "by": "linkuser",
            "score": 250,
            "descendants": 120,
            "time": 1234567890,
            "url": "https://example.com/ai-breakthrough",
            "kids": [2001, 2002],
        }

        with (
            patch.object(
                hn_strategy, "_fetch_item_data", new_callable=AsyncMock
            ) as mock_fetch_item,
            patch.object(
                hn_strategy, "_fetch_comments", new_callable=AsyncMock
            ) as mock_fetch_comments,
        ):
            mock_fetch_item.return_value = mock_item_data
            mock_fetch_comments.return_value = []

            result = hn_strategy.extract_data("", url)

            assert result["title"] == "New AI breakthrough announced"
            assert result["hn_linked_url"] == "https://example.com/ai-breakthrough"
            assert result["is_hn_text_post"] is False
            assert result["requires_content_fetch"] is True
            assert result["content_url_to_fetch"] == "https://example.com/ai-breakthrough"

    def test_prepare_for_llm_with_linked_content(self, hn_strategy, mock_http_client):
        """Test preparing data for LLM with linked article content."""
        extracted_data = {
            "title": "Test Article",
            "text_content": "[Linked article: https://example.com/article]",
            "requires_content_fetch": True,
            "content_url_to_fetch": "https://example.com/article",
            "hn_score": 100,
            "hn_comments_count": 50,
            "hn_submitter": "testuser",
            "hn_discussion_url": "https://news.ycombinator.com/item?id=12345",
            "hn_comments_raw": "Comment 1 by user1:\nGreat article!",
            "is_hn_text_post": False,
        }

        # Mock the HEAD request and HTML strategy
        mock_response = MagicMock()
        mock_response.headers = httpx.Headers({"content-type": "text/html"})
        # http_client uses 'request' method
        mock_http_client.request = MagicMock(return_value=mock_response)

        with patch.object(hn_strategy.html_strategy, "extract_data") as mock_extract:
            mock_extract.return_value = {
                "text_content": "This is the actual article content about AI breakthroughs...",
            }

            result = hn_strategy.prepare_for_llm(extracted_data)

            assert "This is the actual article content" in result["content_to_summarize"]
            assert "HackerNews Discussion Context:" in result["content_to_summarize"]
            assert "Score: 100 points" in result["content_to_summarize"]
            assert "--- HACKERNEWS COMMENTS ---" in result["content_to_summarize"]
            assert result["content_type"] == "hackernews"
            assert result["hn_metadata"]["score"] == 100
            assert result["hn_metadata"]["has_comments"] is True

    def test_prepare_for_llm_text_post(self, hn_strategy):
        """Test preparing data for LLM with text post (no linked content)."""
        extracted_data = {
            "title": "Ask HN: Best practices?",
            "text_content": "What are the best practices for X?",
            "requires_content_fetch": False,
            "hn_score": 50,
            "hn_comments_count": 25,
            "hn_submitter": "askuser",
            "hn_discussion_url": "https://news.ycombinator.com/item?id=12345",
            "hn_comments_raw": "No comments available.",
            "is_hn_text_post": True,
        }

        result = hn_strategy.prepare_for_llm(extracted_data)

        assert "--- POST CONTENT ---" in result["content_to_summarize"]
        assert "What are the best practices" in result["content_to_summarize"]
        assert result["hn_metadata"]["has_comments"] is False
