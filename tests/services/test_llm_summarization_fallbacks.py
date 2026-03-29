"""Tests for strict summarization failure behavior."""

from __future__ import annotations

import pytest

from app.services import llm_summarization


class _AgentWithError:
    def __init__(self, error: Exception):
        self._error = error

    def run_sync(self, _message: str):
        raise self._error


def test_summarize_raises_on_precondition_error(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_get_basic_agent(model_spec, output_type, system_prompt):  # noqa: ANN001
        del output_type, system_prompt
        calls.append(model_spec)
        return _AgentWithError(
            RuntimeError(
                "status_code: 400 FAILED_PRECONDITION - "
                "User location is not supported for the API use."
            )
        )

    monkeypatch.setattr(llm_summarization, "get_basic_agent", _fake_get_basic_agent)

    summarizer = llm_summarization.ContentSummarizer()
    with pytest.raises(RuntimeError, match="FAILED_PRECONDITION"):
        summarizer.summarize(
            "A short body of content for testing",
            content_type="article",
            provider_override="google",
            model_hint="gemini-3-pro-preview",
            content_id=123,
        )

    assert calls == ["google-gla:gemini-3-pro-preview"]


def test_summarize_raises_on_context_length_error(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_get_basic_agent(model_spec, output_type, system_prompt):  # noqa: ANN001
        del output_type, system_prompt
        calls.append(model_spec)
        return _AgentWithError(RuntimeError("maximum context length exceeded"))

    monkeypatch.setattr(llm_summarization, "get_basic_agent", _fake_get_basic_agent)

    summarizer = llm_summarization.ContentSummarizer()
    with pytest.raises(RuntimeError, match="maximum context length exceeded"):
        summarizer.summarize(
            "B" * 5000,
            content_type="article",
            model_hint="gpt-5.4",
            content_id=124,
        )

    assert calls == ["openai:gpt-5.4"]


def test_summarize_raises_when_model_is_unconfigured(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_get_basic_agent(model_spec, output_type, system_prompt):  # noqa: ANN001
        del output_type, system_prompt
        calls.append(model_spec)
        return _AgentWithError(ValueError("OPENAI_API_KEY not configured in settings."))

    monkeypatch.setattr(llm_summarization, "get_basic_agent", _fake_get_basic_agent)

    summarizer = llm_summarization.ContentSummarizer()
    with pytest.raises(ValueError, match="OPENAI_API_KEY not configured"):
        summarizer.summarize(
            "A short body of content for testing",
            content_type="article",
            model_hint="gpt-5.4",
            content_id=125,
        )

    assert calls == ["openai:gpt-5.4"]


def test_summarize_raises_when_agent_returns_no_output(monkeypatch) -> None:
    class _EmptyResult:
        output = None
        data = None

    class _Agent:
        def run_sync(self, _message: str):
            return _EmptyResult()

    monkeypatch.setattr(llm_summarization, "get_basic_agent", lambda *args, **kwargs: _Agent())

    summarizer = llm_summarization.ContentSummarizer()
    with pytest.raises(ValueError, match="returned no output"):
        summarizer.summarize(
            "A short body of content for testing",
            content_type="article",
            content_id=126,
        )
