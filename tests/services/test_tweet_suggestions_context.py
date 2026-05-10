"""Tests for tweet suggestion context extraction and response parsing."""

import json
from unittest.mock import MagicMock

from app.models.contracts import ContentType
from app.services.tweet_suggestions import (
    _extract_content_context,
    _parse_suggestions_response,
    _validate_and_truncate_tweets,
)


class TestContentContextExtraction:
    """Tests for extracting content context for tweet generation."""

    def test_extract_article_context(self) -> None:
        """Extract context from article content."""
        content = MagicMock()
        content.id = 1
        content.content_type = ContentType.ARTICLE
        content.url = "https://example.com/final-article"
        content.display_title = "Great Article Title"
        content.source = "Tech Blog"
        content.platform = "substack"
        content.short_summary = None
        content.summary = None
        content.metadata = {
            "source": "Tech Blog",
            "platform": "substack",
            "final_url_after_redirects": "https://example.com/final-article",
            "summary": {
                "title": "Great Article Title",
                "overview": "This is the overview text.",
                "bullet_points": [
                    {"text": "First key point"},
                    {"text": "Second key point"},
                ],
            },
        }

        context = _extract_content_context(content)

        assert context["title"] == "Great Article Title"
        assert context["source"] == "Tech Blog"
        assert context["platform"] == "substack"
        assert context["url"] == "https://example.com/final-article"
        assert "overview" in context["summary"].lower()
        assert "First key point" in context["key_points"]

    def test_extract_news_context(self) -> None:
        """Extract context from news content."""
        content = MagicMock()
        content.id = 2
        content.content_type = ContentType.NEWS
        content.url = "https://example.com/the-article"
        content.display_title = "News Title"
        content.source = "Hacker News"
        content.platform = "hackernews"
        content.short_summary = None
        content.summary = None
        content.metadata = {
            "source": "Hacker News",
            "platform": "hackernews",
            "article": {
                "url": "https://example.com/the-article",
                "title": "The Real Article",
            },
            "summary": {
                "title": "News Title",
                "article_url": "https://example.com/the-article",
                "summary": "News overview",
                "key_points": ["Point 1", "Point 2"],
            },
        }

        context = _extract_content_context(content)

        assert context["title"] == "News Title"
        assert "the-article" in context["url"]

    def test_extract_context_fallbacks(self) -> None:
        """Context extraction uses fallbacks for missing data."""
        content = MagicMock()
        content.id = 3
        content.content_type = ContentType.ARTICLE
        content.url = "https://example.com/article"
        content.display_title = "Basic Title"
        content.source = None
        content.platform = None
        content.short_summary = None
        content.summary = None
        content.metadata = {}

        context = _extract_content_context(content)

        assert context["title"] == "Basic Title"
        assert context["source"] == "Unknown"
        assert context["platform"] == "web"
        assert context["url"] == "https://example.com/article"
        assert context["quotes"] == "N/A"
        assert context["questions"] == "N/A"
        assert context["counter_arguments"] == "N/A"

    def test_extract_context_with_quotes_questions_counterargs(self) -> None:
        """Context extraction includes quotes, questions, and counter-arguments."""
        content = MagicMock()
        content.id = 4
        content.content_type = ContentType.ARTICLE
        content.url = "https://example.com/article"
        content.display_title = "Article With Full Summary"
        content.source = "Tech Blog"
        content.platform = "substack"
        content.short_summary = None
        content.summary = None
        content.metadata = {
            "summary": {
                "title": "Article With Full Summary",
                "overview": "This is a comprehensive overview.",
                "bullet_points": [{"text": "Key insight one"}],
                "quotes": [
                    {"text": "This is a notable quote from the article."},
                    {"text": "Another important quote."},
                ],
                "questions": [
                    "What are the implications for the industry?",
                    "How might this affect consumers?",
                ],
                "counter_arguments": [
                    "Critics argue this approach has limitations.",
                    "Some experts suggest alternative solutions.",
                ],
            },
        }

        context = _extract_content_context(content)

        assert "notable quote" in context["quotes"]
        assert "important quote" in context["quotes"]
        assert "implications for the industry" in context["questions"]
        assert "affect consumers" in context["questions"]
        assert "limitations" in context["counter_arguments"]
        assert "alternative solutions" in context["counter_arguments"]


class TestResponseParsing:
    """Tests for parsing LLM responses."""

    def test_parse_valid_json(self) -> None:
        """Parse valid JSON response."""
        response = json.dumps(
            {
                "suggestions": [
                    {"id": 1, "text": "Tweet 1", "style_label": "insightful"},
                    {"id": 2, "text": "Tweet 2", "style_label": "provocative"},
                    {"id": 3, "text": "Tweet 3", "style_label": "reflective"},
                ]
            }
        )

        result = _parse_suggestions_response(response)

        assert result is not None
        assert len(result) == 3
        assert result[0]["text"] == "Tweet 1"

    def test_parse_json_in_markdown_fence(self) -> None:
        """Parse JSON wrapped in markdown code fences."""
        response = """```json
{
  "suggestions": [
    {"id": 1, "text": "Tweet 1", "style_label": "a"},
    {"id": 2, "text": "Tweet 2", "style_label": "b"},
    {"id": 3, "text": "Tweet 3", "style_label": "c"}
  ]
}
```"""

        result = _parse_suggestions_response(response)

        assert result is not None
        assert len(result) == 3

    def test_parse_json_in_plain_fence(self) -> None:
        """Parse JSON wrapped in plain code fences."""
        response = """```
{
  "suggestions": [
    {"id": 1, "text": "Tweet 1", "style_label": "a"},
    {"id": 2, "text": "Tweet 2", "style_label": "b"},
    {"id": 3, "text": "Tweet 3", "style_label": "c"}
  ]
}
```"""

        result = _parse_suggestions_response(response)

        assert result is not None
        assert len(result) == 3

    def test_parse_invalid_json(self) -> None:
        """Invalid JSON returns None."""
        response = "This is not valid JSON at all"

        result = _parse_suggestions_response(response)

        assert result is None

    def test_parse_wrong_count(self) -> None:
        """Wrong number of suggestions returns None."""
        response = json.dumps(
            {
                "suggestions": [
                    {"id": 1, "text": "Only one tweet", "style_label": "a"},
                ]
            }
        )

        result = _parse_suggestions_response(response)

        assert result is None


class TestTweetValidation:
    """Tests for tweet validation and truncation."""

    def test_validate_normal_tweets(self) -> None:
        """Tweets under 400 chars pass through unchanged."""
        suggestions = [
            {"id": 1, "text": "Short tweet", "style_label": "a"},
            {"id": 2, "text": "Another tweet", "style_label": "b"},
            {"id": 3, "text": "Third tweet", "style_label": "c"},
        ]

        result = _validate_and_truncate_tweets(suggestions)

        assert len(result) == 3
        assert result[0].text == "Short tweet"

    def test_truncate_long_tweet(self) -> None:
        """Tweets over 400 chars are truncated with ellipsis."""
        long_text = "A" * 450
        suggestions = [
            {"id": 1, "text": long_text, "style_label": "a"},
            {"id": 2, "text": "Normal tweet", "style_label": "b"},
            {"id": 3, "text": "Another normal", "style_label": "c"},
        ]

        result = _validate_and_truncate_tweets(suggestions)

        assert len(result[0].text) == 400
        assert result[0].text.endswith("...")

    def test_truncate_with_custom_max_chars(self) -> None:
        """Tweets are truncated based on custom max_chars."""
        text_200 = "A" * 200
        suggestions = [
            {"id": 1, "text": text_200, "style_label": "a"},
            {"id": 2, "text": "Normal", "style_label": "b"},
            {"id": 3, "text": "Another", "style_label": "c"},
        ]

        result = _validate_and_truncate_tweets(suggestions, max_chars=180)
        assert len(result[0].text) == 180
        assert result[0].text.endswith("...")

        result = _validate_and_truncate_tweets(suggestions, max_chars=280)
        assert len(result[0].text) == 200
        assert not result[0].text.endswith("...")

    def test_preserve_style_labels(self) -> None:
        """Style labels are preserved in validation."""
        suggestions = [
            {"id": 1, "text": "Tweet 1", "style_label": "insightful"},
            {"id": 2, "text": "Tweet 2", "style_label": None},
            {"id": 3, "text": "Tweet 3", "style_label": "bold"},
        ]

        result = _validate_and_truncate_tweets(suggestions)

        assert result[0].style_label == "insightful"
        assert result[1].style_label is None
        assert result[2].style_label == "bold"
