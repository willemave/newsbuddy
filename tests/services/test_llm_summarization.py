from types import SimpleNamespace

import pytest

from app.models.metadata import ContentType, EditorialNarrativeSummary, NewsSummary
from app.models.schema import LlmUsageRecord
from app.services import llm_summarization


class FakeResult:
    def __init__(self, output, usage=None):
        self.output = output
        self.data = output
        self._usage = usage

    def usage(self):  # noqa: ANN001
        return self._usage


class FakeAgent:
    def __init__(self, data, *, usage=None):
        self._data = data
        self._usage = usage
        self.last_prompt: str | None = None

    def run_sync(self, prompt: str):
        self.last_prompt = prompt
        return FakeResult(self._data, usage=self._usage)


def _editorial_summary(
    *,
    quotes: list[dict[str, str | None]] | None = None,
) -> EditorialNarrativeSummary:
    return EditorialNarrativeSummary(
        title="Test Title",
        editorial_narrative=(
            "This is a dense editorial summary with enough concrete detail to satisfy "
            "the validation rules while still being compact and easy to reuse in tests. "
            "It names consequences, tradeoffs, and evidence rather than filler.\n\n"
            "The second paragraph adds constraints, execution implications, and signal "
            "about why the source matters, which keeps the payload valid for narrative "
            "summary parsing."
        ),
        quotes=quotes
        or [
            {
                "text": "This is a meaningful supporting quote from the source material.",
                "attribution": "Source A",
            },
            {
                "text": "This is another meaningful quote with enough detail to validate.",
                "attribution": "Source B",
            },
        ],
        key_points=[
            {"point": "First key point with enough detail to be valid."},
            {"point": "Second key point with enough detail to be valid."},
            {"point": "Third key point with enough detail to be valid."},
            {"point": "Fourth key point with enough detail to be valid."},
        ],
    )


def _news_summary() -> NewsSummary:
    return NewsSummary(
        title="News Title",
        article_url="https://example.com",
        key_points=["One"],
        summary="Short news summary.",
    )


def _agent_output_for_type(output_type):
    if output_type is NewsSummary:
        return _news_summary()
    return _editorial_summary()


def test_summarize_uses_agent_and_title_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = _editorial_summary()
    fake_agent = FakeAgent(summary)
    monkeypatch.setattr(llm_summarization, "get_basic_agent", lambda *args, **kwargs: fake_agent)

    summarizer = llm_summarization.ContentSummarizer()

    result = summarizer.summarize("Body", content_type=ContentType.ARTICLE, title="Title")

    assert result == summary
    assert fake_agent.last_prompt is not None
    assert "Title: Title" in fake_agent.last_prompt


def test_summarize_news_uses_news_summary_output_type(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_output_types = []

    def fake_get_basic_agent(model_spec, output_type, system_prompt):  # noqa: ANN001
        del model_spec, system_prompt
        captured_output_types.append(output_type)
        return FakeAgent(_agent_output_for_type(output_type))

    monkeypatch.setattr(llm_summarization, "get_basic_agent", fake_get_basic_agent)

    summarizer = llm_summarization.ContentSummarizer()
    result = summarizer.summarize("News body", content_type="news")

    assert isinstance(result, NewsSummary)
    assert result.title == "News Title"
    assert captured_output_types == [NewsSummary]


def test_content_summarizer_resolves_default_models(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_resolves: list[tuple[str | None, str | None]] = []
    captured_model_specs: list[str] = []

    def fake_resolve(provider: str | None, hint: str | None) -> tuple[str, str]:
        captured_resolves.append((provider, hint))
        return provider or "openai", f"{provider}:{hint}"

    def fake_get_basic_agent(model_spec, output_type, system_prompt):  # noqa: ANN001
        del system_prompt
        captured_model_specs.append(model_spec)
        return FakeAgent(_agent_output_for_type(output_type))

    monkeypatch.setattr(llm_summarization, "get_basic_agent", fake_get_basic_agent)

    summarizer = llm_summarization.ContentSummarizer(_model_resolver=fake_resolve)
    summarizer.summarize("body", content_type=ContentType.NEWS)

    assert captured_resolves == [("google", "gemini-3.1-flash-lite-preview")]
    assert captured_model_specs == ["google:gemini-3.1-flash-lite-preview"]


def test_content_summarizer_uses_gpt_5_4_mini_for_articles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_resolves: list[tuple[str | None, str | None]] = []
    captured_model_specs: list[str] = []

    def fake_resolve(provider: str | None, hint: str | None) -> tuple[str, str]:
        captured_resolves.append((provider, hint))
        return provider or "openai", f"{provider}:{hint}"

    def fake_get_basic_agent(model_spec, output_type, system_prompt):  # noqa: ANN001
        del system_prompt
        captured_model_specs.append(model_spec)
        return FakeAgent(_agent_output_for_type(output_type))

    monkeypatch.setattr(llm_summarization, "get_basic_agent", fake_get_basic_agent)

    summarizer = llm_summarization.ContentSummarizer(_model_resolver=fake_resolve)
    summarizer.summarize("body", content_type=ContentType.ARTICLE)

    assert captured_resolves == [("openai", "gpt-5.4-mini")]
    assert captured_model_specs == ["openai:gpt-5.4-mini"]


def test_content_summarizer_respects_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_resolves: list[tuple[str | None, str | None]] = []
    captured_model_specs: list[str] = []

    def fake_resolve(provider: str | None, hint: str | None) -> tuple[str, str]:
        captured_resolves.append((provider, hint))
        return provider or "anthropic", f"{provider}:{hint}"

    def fake_get_basic_agent(model_spec, output_type, system_prompt):  # noqa: ANN001
        del system_prompt
        captured_model_specs.append(model_spec)
        return FakeAgent(_agent_output_for_type(output_type))

    monkeypatch.setattr(llm_summarization, "get_basic_agent", fake_get_basic_agent)

    summarizer = llm_summarization.ContentSummarizer(_model_resolver=fake_resolve)
    summarizer.summarize(
        "body",
        content_type=ContentType.ARTICLE,
        provider_override="google",
        model_hint="gemini-1.5",
    )

    assert captured_resolves == [("google", "gemini-1.5")]
    assert captured_model_specs == ["google:gemini-1.5"]


def test_summarize_truncates_long_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = _editorial_summary()
    fake_agent = FakeAgent(summary)
    monkeypatch.setattr(llm_summarization, "get_basic_agent", lambda *args, **kwargs: fake_agent)
    monkeypatch.setattr(llm_summarization, "MAX_SUMMARIZATION_PAYLOAD_CHARS", 120)

    summarizer = llm_summarization.ContentSummarizer()
    result = summarizer.summarize(
        "START " + ("A" * 200) + " END",
        content_type=ContentType.ARTICLE,
    )

    assert result == summary
    assert fake_agent.last_prompt is not None
    assert "START" in fake_agent.last_prompt
    assert "END" in fake_agent.last_prompt
    assert "[... CONTENT TRUNCATED ...]" in fake_agent.last_prompt


def test_summarize_prunes_short_editorial_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = _editorial_summary()
    summary.quotes = [
        SimpleNamespace(text="short", attribution="A"),
        SimpleNamespace(
            text="This quote is long enough to survive finalization.",
            attribution="B",
        ),
    ]
    fake_agent = FakeAgent(summary)
    monkeypatch.setattr(llm_summarization, "get_basic_agent", lambda *args, **kwargs: fake_agent)

    summarizer = llm_summarization.ContentSummarizer()
    result = summarizer.summarize("Body", content_type=ContentType.ARTICLE)

    assert result is not None
    assert len(result.quotes) == 1
    assert result.quotes[0].text == "This quote is long enough to survive finalization."


def test_summarize_returns_none_for_empty_payload() -> None:
    summarizer = llm_summarization.ContentSummarizer()
    assert summarizer.summarize("", content_type=ContentType.ARTICLE) is None


def test_summarize_persists_usage_when_db_and_metadata_provided(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = _editorial_summary()
    fake_agent = FakeAgent(
        summary,
        usage=SimpleNamespace(input_tokens=120, output_tokens=30, total_tokens=150),
    )
    monkeypatch.setattr(llm_summarization, "get_basic_agent", lambda *args, **kwargs: fake_agent)

    summarizer = llm_summarization.ContentSummarizer()
    result = summarizer.summarize(
        "Body",
        content_type=ContentType.ARTICLE,
        db=db_session,
        usage_persist={
            "feature": "summarization",
            "operation": "summarization.llm_summarization",
            "source": "queue",
            "content_id": 42,
        },
    )

    assert result == summary
    row = db_session.query(LlmUsageRecord).one()
    assert row.feature == "summarization"
    assert row.operation == "summarization.llm_summarization"
    assert row.source == "queue"
    assert row.content_id == 42
    assert row.total_tokens == 150
