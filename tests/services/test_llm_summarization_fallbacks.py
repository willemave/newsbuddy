"""Tests for summarization fallback behavior."""

from __future__ import annotations

from types import SimpleNamespace

from app.services import llm_summarization
from app.services.llm_summarization import (
    FALLBACK_SUMMARIZATION_MODEL,
    SummarizationRequest,
    summarize_content,
)


def test_summarize_content_falls_back_cross_provider_on_precondition_error(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_agent_factory(model_spec: str, *_args, **_kwargs):  # noqa: ANN001
        class _Agent:
            def run_sync(self, _message: str):  # noqa: ANN001
                calls.append(model_spec)
                if model_spec.startswith("google-gla:"):
                    raise RuntimeError(
                        "status_code: 400 FAILED_PRECONDITION - "
                        "User location is not supported for the API use."
                    )
                return SimpleNamespace(output={"title": "Fallback summary"})

        return _Agent()

    monkeypatch.setattr(llm_summarization, "get_summarization_agent", _fake_agent_factory)

    request = SummarizationRequest(
        content="A short body of content for testing",
        content_type="article",
        model_spec="google-gla:gemini-3-pro-preview",
        content_id=123,
    )

    result = summarize_content(request)

    assert result is not None
    assert calls[0] == "google-gla:gemini-3-pro-preview"
    assert calls[1] == "openai:gpt-4o"


def test_summarize_content_uses_context_fallback_model(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_agent_factory(model_spec: str, *_args, **_kwargs):  # noqa: ANN001
        class _Agent:
            def run_sync(self, _message: str):  # noqa: ANN001
                calls.append(model_spec)
                if model_spec != FALLBACK_SUMMARIZATION_MODEL:
                    raise RuntimeError("maximum context length exceeded")
                return SimpleNamespace(output={"title": "Context fallback summary"})

        return _Agent()

    monkeypatch.setattr(llm_summarization, "get_summarization_agent", _fake_agent_factory)

    request = SummarizationRequest(
        content="B" * 5000,
        content_type="article",
        model_spec="openai:gpt-5.2",
        content_id=124,
    )

    result = summarize_content(request)

    assert result is not None
    assert "openai:gpt-5.2" in calls
    assert FALLBACK_SUMMARIZATION_MODEL in calls


def test_summarize_content_returns_none_when_openai_fallback_is_unconfigured(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def _fake_agent_factory(model_spec: str, *_args, **_kwargs):  # noqa: ANN001
        class _Agent:
            def run_sync(self, _message: str):  # noqa: ANN001
                calls.append(model_spec)
                if model_spec.startswith("google-gla:"):
                    raise RuntimeError(
                        "status_code: 400 FAILED_PRECONDITION - "
                        "User location is not supported for the API use."
                    )
                if model_spec.startswith("openai:"):
                    raise ValueError("OPENAI_API_KEY not configured in settings.")
                return SimpleNamespace(output={"title": "Unexpected fallback summary"})

        return _Agent()

    monkeypatch.setattr(llm_summarization, "get_summarization_agent", _fake_agent_factory)

    request = SummarizationRequest(
        content="A short body of content for testing",
        content_type="article",
        model_spec="google-gla:gemini-3-pro-preview",
        content_id=125,
    )

    result = summarize_content(request)

    assert result is None
    assert calls == [
        "google-gla:gemini-3-pro-preview",
        "openai:gpt-4o",
    ]


def test_summarize_content_recovers_from_event_loop_binding_error(monkeypatch) -> None:
    calls: list[str] = []
    run_invocations = {"count": 0}

    def _fake_agent_factory(model_spec: str, *_args, **_kwargs):  # noqa: ANN001
        calls.append(model_spec)

        class _Agent:
            def run_sync(self, _message: str):  # noqa: ANN001
                run_invocations["count"] += 1
                if run_invocations["count"] == 1:
                    raise RuntimeError("is bound to a different event loop")
                return SimpleNamespace(output={"title": "Recovered summary"})

        return _Agent()

    monkeypatch.setattr(llm_summarization, "get_summarization_agent", _fake_agent_factory)

    request = SummarizationRequest(
        content="A short body of content for testing",
        content_type="article",
        model_spec="openai:gpt-5.2",
        content_id=126,
    )

    result = summarize_content(request)

    assert result is not None
    assert run_invocations["count"] == 2
    assert calls == ["openai:gpt-5.2", "openai:gpt-5.2"]
