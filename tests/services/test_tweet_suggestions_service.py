"""Service-level tests for tweet suggestion generation."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import HttpUrl, TypeAdapter
from pydantic_ai import PromptedOutput

from app.constants import TWEET_MODELS
from app.models.contracts import ContentStatus, ContentType
from app.models.domain.content import ContentData
from app.services.tweet_suggestions import (
    TWEET_MODEL,
    TweetSuggestionLLM,
    TweetSuggestionService,
    TweetSuggestionsPayload,
)


def _url(value: str) -> HttpUrl:
    return TypeAdapter(HttpUrl).validate_python(value)


class TestTweetSuggestionService:
    """Integration tests for the TweetSuggestionService."""

    @patch("app.services.tweet_suggestions.Agent.run_sync")
    def test_generate_suggestions_success(self, mock_run_sync, monkeypatch) -> None:
        """Successfully generate tweet suggestions."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
        mock_payload = TweetSuggestionsPayload(
            suggestions=[
                TweetSuggestionLLM(id=1, text="Great article!", style_label="a"),
                TweetSuggestionLLM(id=2, text="Must read this", style_label="b"),
                TweetSuggestionLLM(id=3, text="Interesting take", style_label="c"),
            ]
        )
        mock_result = MagicMock()
        mock_result.output = mock_payload
        mock_result.usage.return_value = MagicMock(input_tokens=100, output_tokens=50)
        mock_run_sync.return_value = mock_result

        content = MagicMock()
        content.id = 1
        content.content_type = ContentType.ARTICLE
        content.url = "https://example.com/article"
        content.display_title = "Test Article"
        content.source = "Tech Blog"
        content.platform = "substack"
        content.short_summary = None
        content.summary = None
        content.metadata = {
            "source": "Tech Blog",
            "summary": {
                "title": "Article Title",
                "overview": "This is the overview text.",
                "bullet_points": [{"text": "Key point"}],
            },
        }

        service = TweetSuggestionService()
        result = service.generate_suggestions(content, creativity=5, length="short")

        assert result is not None
        assert result.content_id == 1
        assert result.creativity == 5
        assert result.length == "short"
        assert result.model == TWEET_MODEL
        assert len(result.suggestions) == 3
        assert result.suggestions[0].text == "Great article!"

    @patch("app.services.tweet_suggestions.Agent.run_sync")
    def test_generate_suggestions_podcast_supported(self, mock_run_sync, monkeypatch) -> None:
        """Podcasts are supported for tweet suggestions."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
        mock_payload = TweetSuggestionsPayload(
            suggestions=[
                TweetSuggestionLLM(id=1, text="Podcast insight 1", style_label="a"),
                TweetSuggestionLLM(id=2, text="Podcast insight 2", style_label="b"),
                TweetSuggestionLLM(id=3, text="Podcast insight 3", style_label="c"),
            ]
        )
        mock_result = MagicMock()
        mock_result.output = mock_payload
        mock_result.usage.return_value = None
        mock_run_sync.return_value = mock_result

        content = ContentData(
            id=1,
            content_type=ContentType.PODCAST,
            url=_url("https://example.com/podcast"),
            title="Test Podcast",
            status=ContentStatus.COMPLETED,
            metadata={
                "summary_kind": "long_structured",
                "summary_version": 1,
                "summary": {
                    "title": "Podcast Episode",
                    "overview": (
                        "This overview is intentionally long enough to satisfy structured "
                        "summary validation for podcast content."
                    ),
                    "bullet_points": [
                        {"text": "Key takeaway from the episode.", "category": "key_finding"},
                        {
                            "text": "Second highlight from the discussion.",
                            "category": "insight",
                        },
                        {
                            "text": "Third point explaining the main theme.",
                            "category": "context",
                        },
                    ],
                    "quotes": [],
                    "topics": [],
                },
            },
        )

        service = TweetSuggestionService()
        result = service.generate_suggestions(content, creativity=5)

        assert result is not None
        assert len(result.suggestions) == 3

    @patch("app.services.tweet_suggestions.Agent.run_sync")
    def test_generate_suggestions_accepts_raw_json_text_output(
        self, mock_run_sync, monkeypatch
    ) -> None:
        """Prompted model output can be parsed from raw JSON text."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
        mock_result = MagicMock()
        mock_result.output = """
        ```json
        {
          "suggestions": [
            {"id": 1, "text": "Raw JSON tweet one", "style_label": "clear"},
            {"id": 2, "text": "Raw JSON tweet two", "style_label": "sharp"},
            {"id": 3, "text": "Raw JSON tweet three", "style_label": "useful"}
          ]
        }
        ```
        """
        mock_result.usage.return_value = None
        mock_run_sync.return_value = mock_result

        content = MagicMock()
        content.id = 1
        content.content_type = ContentType.ARTICLE
        content.url = "https://example.com/article"
        content.display_title = "Test Article"
        content.source = "Tech Blog"
        content.platform = "web"
        content.short_summary = "Short summary"
        content.summary = None
        content.metadata = {}

        service = TweetSuggestionService()
        result = service.generate_suggestions(content, creativity=5, llm_provider="openrouter")

        assert result is not None
        assert len(result.suggestions) == 3
        assert result.suggestions[0].text == "Raw JSON tweet one"


class TestTweetModelSelection:
    """Tests for tweet model resolution."""

    def test_default_model_used(self) -> None:
        """Default model spec is returned when provider is not set."""
        service = TweetSuggestionService()
        assert service._get_model_for_provider(None) == TWEET_MODEL

    def test_provider_specific_model_used(self) -> None:
        """Provider override uses the mapped model spec when available."""
        service = TweetSuggestionService()
        for provider, model_spec in TWEET_MODELS.items():
            assert service._get_model_for_provider(provider) == model_spec

    def test_supported_agents_use_prompted_json_output(self, monkeypatch) -> None:
        """OpenAI, Anthropic, and OpenRouter avoid tool-output-only generation."""
        captured: dict[str, object] = {}

        def fake_get_basic_agent(model_spec, output_type, system_prompt):  # noqa: ANN001
            captured["model_spec"] = model_spec
            captured["output_type"] = output_type
            captured["system_prompt"] = system_prompt
            return MagicMock()

        monkeypatch.setattr(
            "app.services.tweet_suggestions.get_basic_agent",
            fake_get_basic_agent,
        )

        service = TweetSuggestionService()
        for model_spec in (
            "openai:gpt-5.6-terra",
            "anthropic:claude-opus-4-6",
            "openrouter:deepseek/deepseek-v4-flash",
        ):
            service._build_agent("system prompt", model_spec)
            output_type = captured["output_type"]
            assert isinstance(output_type, PromptedOutput)
            assert output_type.outputs is TweetSuggestionsPayload

    def test_google_provider_is_rejected(self) -> None:
        service = TweetSuggestionService()

        with pytest.raises(ValueError, match="Unsupported tweet LLM provider: google"):
            service._get_model_for_provider("google")
