import pytest

from app.models.metadata import ContentType, NewsSummary, StructuredSummary, SummaryBulletPoint
from app.services import llm_summarization


class FakeResult:
    def __init__(self, output):
        self.output = output
        # Compatibility for older pydantic-ai AgentRunResult shims in tests.
        self.data = output


class FakeAgent:
    def __init__(self, data):
        self._data = data
        self.last_prompt: str | None = None

    def run_sync(self, prompt: str):
        self.last_prompt = prompt
        return FakeResult(self._data)


def _structured_summary() -> StructuredSummary:
    return StructuredSummary(
        title="Test Title",
        overview="This is a test overview that is sufficiently long.",
        bullet_points=[
            SummaryBulletPoint(text="First bullet point text.", category="insight"),
            SummaryBulletPoint(text="Second bullet point text.", category="detail"),
            SummaryBulletPoint(text="Third bullet point text.", category="detail"),
        ],
        quotes=[],
        topics=["topic"],
        questions=[],
        counter_arguments=[],
    )


def test_summarize_content_uses_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = _structured_summary()
    fake_agent = FakeAgent(summary)
    monkeypatch.setattr(
        llm_summarization, "get_summarization_agent", lambda *args, **kwargs: fake_agent
    )

    request = llm_summarization.SummarizationRequest(
        content="Body",
        content_type=ContentType.ARTICLE,
        model_spec="gpt-5-mini",
        title="Title",
    )

    result = llm_summarization.summarize_content(request)

    assert result == summary
    assert fake_agent.last_prompt is not None
    assert "Title: Title" in fake_agent.last_prompt


def test_summarize_news_uses_news_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    news_summary = NewsSummary(
        title="News Title", article_url="https://example.com", key_points=["One"]
    )
    fake_agent = FakeAgent(news_summary)
    monkeypatch.setattr(
        llm_summarization, "get_summarization_agent", lambda *args, **kwargs: fake_agent
    )

    request = llm_summarization.SummarizationRequest(
        content="News body",
        content_type="news",
        model_spec="claude-haiku-4-5-20251001",
    )

    result = llm_summarization.summarize_content(request)

    assert isinstance(result, NewsSummary)
    assert result.title == "News Title"


def test_content_summarizer_resolves_default_models(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str | None, str | None]] = []

    def fake_resolve(provider: str | None, hint: str | None) -> tuple[str, str]:
        captured.append((provider, hint))
        return provider or "openai", f"{provider}:{hint}"

    def fake_summarize(request: llm_summarization.SummarizationRequest):
        return request.model_spec

    monkeypatch.setattr(llm_summarization, "summarize_content", fake_summarize)

    summarizer = llm_summarization.ContentSummarizer(_model_resolver=fake_resolve)

    model_used = summarizer.summarize("body", content_type=ContentType.NEWS)

    assert model_used == "google:gemini-3.1-flash-lite-preview"
    assert captured == [("google", "gemini-3.1-flash-lite-preview")]


def test_content_summarizer_respects_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str | None, str | None]] = []

    def fake_resolve(provider: str | None, hint: str | None) -> tuple[str, str]:
        captured.append((provider, hint))
        return provider or "anthropic", f"{provider}:{hint}"

    def fake_summarize(request: llm_summarization.SummarizationRequest):
        return request.model_spec

    monkeypatch.setattr(llm_summarization, "summarize_content", fake_summarize)

    summarizer = llm_summarization.ContentSummarizer(_model_resolver=fake_resolve)

    model_used = summarizer.summarize(
        "body",
        content_type=ContentType.ARTICLE,
        provider_override="google",
        model_hint="gemini-1.5",
    )

    assert model_used == "google:gemini-1.5"
    assert captured == [("google", "gemini-1.5")]


def test_summarize_content_truncates_long_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = _structured_summary()
    fake_agent = FakeAgent(summary)
    monkeypatch.setattr(
        llm_summarization, "get_summarization_agent", lambda *args, **kwargs: fake_agent
    )
    monkeypatch.setattr(llm_summarization, "MAX_SUMMARIZATION_PAYLOAD_CHARS", 120)

    request = llm_summarization.SummarizationRequest(
        content="START " + ("A" * 200) + " END",
        content_type=ContentType.ARTICLE,
        model_spec="gpt-5-mini",
    )

    result = llm_summarization.summarize_content(request)

    assert result == summary
    assert fake_agent.last_prompt is not None
    assert "START" in fake_agent.last_prompt
    assert "END" in fake_agent.last_prompt
    assert "[... CONTENT TRUNCATED ...]" in fake_agent.last_prompt


def test_summarize_content_retries_on_context_length_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that context length errors trigger fallback to a different model."""
    summary = _structured_summary()

    # Track which models were used
    models_used: list[str] = []

    class FlakyAgent:
        """Agent that fails on first call with context_length_exceeded."""

        def __init__(self, output, model_spec: str):
            self._output = output
            self._model_spec = model_spec
            self.calls = 0

        def run_sync(self, prompt: str):
            self.calls += 1
            # Fail on first call (primary model)
            if "gpt-5-mini" in self._model_spec and self.calls == 1:
                raise RuntimeError("context_length_exceeded")
            return FakeResult(self._output)

    def fake_get_agent(model_spec: str, *args, **kwargs):
        models_used.append(model_spec)
        return FlakyAgent(summary, model_spec)

    monkeypatch.setattr(llm_summarization, "get_summarization_agent", fake_get_agent)
    monkeypatch.setattr(llm_summarization, "MAX_SUMMARIZATION_PAYLOAD_CHARS", 80)
    monkeypatch.setattr(llm_summarization, "FALLBACK_SUMMARIZATION_PAYLOAD_CHARS", 40)

    request = llm_summarization.SummarizationRequest(
        content=("X" * 200),
        content_type=ContentType.ARTICLE,
        model_spec="gpt-5-mini",
        content_id="test",
    )

    result = llm_summarization.summarize_content(request)

    assert result == summary
    # Primary model failed, then fallback model was used
    assert len(models_used) == 2
    assert models_used[0] == "gpt-5-mini"
    assert models_used[1] == llm_summarization.FALLBACK_SUMMARIZATION_MODEL
