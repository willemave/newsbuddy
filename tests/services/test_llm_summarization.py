from types import SimpleNamespace

import pytest

from app.models.contracts import ContentType
from app.models.db import VendorUsageRecord
from app.models.metadata.summaries import (
    DiscussionSummary,
    EditorialNarrativeSummary,
    EditorialQuote,
    GeneratedEditorialKeyPoint,
    GeneratedEditorialNarrativeSummary,
    GeneratedNewsSummary,
    NewsSummary,
)
from app.services import llm_summarization


class FakeResult:
    def __init__(self, output: object, usage: object | None = None) -> None:
        self.output = output
        self.data = output
        self._usage = usage

    @property
    def usage(self) -> object | None:
        return self._usage


class FakeAgent:
    def __init__(self, data: object, *, usage: object | None = None) -> None:
        self._data = data
        self._usage = usage
        self.last_prompt: str | None = None

    def run_sync(self, prompt: str) -> FakeResult:
        self.last_prompt = prompt
        return FakeResult(self._data, usage=self._usage)


def _editorial_summary(
    *,
    quotes: list[EditorialQuote] | None = None,
) -> GeneratedEditorialNarrativeSummary:
    return GeneratedEditorialNarrativeSummary(
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
            EditorialQuote(
                text="This is a meaningful supporting quote from the source material.",
                attribution="Source A",
            ),
            EditorialQuote(
                text="This is another meaningful quote with enough detail to validate.",
                attribution="Source B",
            ),
        ],
        key_points=[
            GeneratedEditorialKeyPoint(point="First key point with enough detail."),
            GeneratedEditorialKeyPoint(point="Second key point with enough detail."),
            GeneratedEditorialKeyPoint(point="Third key point with enough detail."),
            GeneratedEditorialKeyPoint(point="Fourth key point with enough detail."),
        ],
        source_details=None,
    )


def _news_summary() -> GeneratedNewsSummary:
    return GeneratedNewsSummary(
        title="News Title",
        article_url="https://example.com",
        key_points=["One concrete source point.", "Second concrete source point."],
        summary="Short news summary.",
    )


def _agent_output_for_type(output_type):
    if output_type in {GeneratedNewsSummary, NewsSummary}:
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
    captured_system_prompts: list[str] = []

    def fake_get_basic_agent(model_spec, output_type, system_prompt):  # noqa: ANN001
        del model_spec
        captured_output_types.append(output_type)
        captured_system_prompts.append(system_prompt)
        return FakeAgent(_agent_output_for_type(output_type))

    monkeypatch.setattr(llm_summarization, "get_basic_agent", fake_get_basic_agent)

    summarizer = llm_summarization.ContentSummarizer()
    result = summarizer.summarize("News body", content_type="news")

    assert isinstance(result, NewsSummary)
    assert result.title == "News Title"
    assert captured_output_types == [GeneratedNewsSummary]
    assert "provided structured output schema" in captured_system_prompts[0].lower()
    assert "direct factual headline" in captured_system_prompts[0].lower()


def test_editorial_generation_uses_strict_generated_output_type() -> None:
    output_type = llm_summarization.resolve_summarization_output_type("editorial_narrative")

    assert output_type is GeneratedEditorialNarrativeSummary


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

    assert captured_resolves == [("openrouter", "deepseek/deepseek-v4-flash")]
    assert captured_model_specs == ["openrouter:deepseek/deepseek-v4-flash"]


def test_content_summarizer_uses_luna_for_articles(
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

    assert captured_resolves == [("openai", "gpt-5.6-luna")]
    assert captured_model_specs == ["openai:gpt-5.6-luna"]


def test_content_summarizer_uses_editorial_model_for_specialized_prompt_types(
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
    summarizer.summarize("body", content_type="editorial_research")

    assert captured_resolves == [("openai", "gpt-5.6-terra")]
    assert captured_model_specs == ["openai:gpt-5.6-terra"]


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
        EditorialQuote(text="short quote!", attribution="A"),
        EditorialQuote(
            text="This quote is long enough to survive finalization.",
            attribution="B",
        ),
    ]
    fake_agent = FakeAgent(summary)
    monkeypatch.setattr(llm_summarization, "get_basic_agent", lambda *args, **kwargs: fake_agent)

    summarizer = llm_summarization.ContentSummarizer()
    result = summarizer.summarize("Body", content_type=ContentType.ARTICLE)

    assert isinstance(result, EditorialNarrativeSummary)
    assert result is not None
    assert len(result.quotes) == 1
    assert result.quotes[0].text == "This quote is long enough to survive finalization."


def test_summarize_returns_none_for_empty_payload() -> None:
    summarizer = llm_summarization.ContentSummarizer()
    assert summarizer.summarize("", content_type=ContentType.ARTICLE) is None


def test_discussion_summary_discards_invalid_urls_from_llm_payload() -> None:
    summary = DiscussionSummary.model_validate(
        {
            "overview": "Commenters compared the tradeoffs and surfaced concrete caveats.",
            "topics": [],
            "notable_links": [
                {
                    "url": "https://example.com/context",
                    "title": "Useful context",
                    "reason": "Adds background.",
                },
                {
                    "url": "not a url",
                    "title": "Malformed",
                    "reason": "Should be dropped.",
                },
                {"title": "Missing URL"},
            ],
            "external_discussion_url": "not a url",
        }
    )

    assert summary.external_discussion_url is None
    assert len(summary.notable_links) == 1
    assert str(summary.notable_links[0].url) == "https://example.com/context"
    assert len(summary.topics) == 1
    assert summary.topics[0].title == "General discussion"


def test_summarize_persists_usage_when_db_and_metadata_provided(
    db_session,
    vendor_usage_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del vendor_usage_db
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
    db_session.commit()
    row = db_session.query(VendorUsageRecord).one()
    assert row.feature == "summarization"
    assert row.operation == "summarization.llm_summarization"
    assert row.source == "queue"
    assert row.content_id == 42
    assert row.total_tokens == 150
