from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import cast

from pydantic_ai.models.instrumented import InstrumentationSettings

from app.services import langfuse_tracing


def _reset_langfuse_state() -> None:
    langfuse_tracing._LANGFUSE_CLIENT = None
    langfuse_tracing._LANGFUSE_INITIALIZED = False
    langfuse_tracing._LANGFUSE_READY = False


def _settings(**overrides: object) -> SimpleNamespace:
    base = {
        "langfuse_enabled": True,
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
        "langfuse_host": "https://cloud.langfuse.com",
        "langfuse_sample_rate": None,
        "langfuse_include_content": True,
        "langfuse_include_binary_content": False,
        "langfuse_instrumentation_version": 2,
        "langfuse_event_mode": "attributes",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_initialize_langfuse_tracing_disabled(monkeypatch) -> None:
    _reset_langfuse_state()
    monkeypatch.setattr(
        langfuse_tracing,
        "get_settings",
        lambda: _settings(langfuse_enabled=False),
    )

    enabled = langfuse_tracing.initialize_langfuse_tracing()

    assert enabled is False
    assert langfuse_tracing._LANGFUSE_READY is False


def test_initialize_langfuse_tracing_missing_keys(monkeypatch) -> None:
    _reset_langfuse_state()
    monkeypatch.setattr(
        langfuse_tracing,
        "get_settings",
        lambda: _settings(langfuse_public_key=None, langfuse_secret_key=None),
    )

    enabled = langfuse_tracing.initialize_langfuse_tracing()

    assert enabled is False
    assert langfuse_tracing._LANGFUSE_READY is False


def test_initialize_langfuse_tracing_success(monkeypatch) -> None:
    _reset_langfuse_state()
    monkeypatch.setattr(langfuse_tracing, "get_settings", lambda: _settings())

    captured: dict[str, object] = {}

    class FakeLangfuse:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        def flush(self) -> None:
            captured["flushed"] = True

    monkeypatch.setattr(langfuse_tracing, "Langfuse", FakeLangfuse)

    def _instrument_all(instrumentation: InstrumentationSettings) -> None:
        captured["instrumentation"] = instrumentation

    monkeypatch.setattr(langfuse_tracing.Agent, "instrument_all", _instrument_all)

    enabled = langfuse_tracing.initialize_langfuse_tracing()

    assert enabled is True
    assert langfuse_tracing._LANGFUSE_READY is True
    assert captured["client_kwargs"] == {
        "public_key": "pk-test",
        "secret_key": "sk-test",
        "host": "https://cloud.langfuse.com",
        "sample_rate": None,
        "tracing_enabled": True,
    }
    assert isinstance(captured["instrumentation"], InstrumentationSettings)


def test_langfuse_trace_context_propagates_attributes(monkeypatch) -> None:
    _reset_langfuse_state()
    langfuse_tracing._LANGFUSE_INITIALIZED = True
    langfuse_tracing._LANGFUSE_READY = True

    captured: dict[str, object] = {}

    @contextmanager
    def _fake_propagate_attributes(**kwargs: object):
        captured["kwargs"] = kwargs
        yield

    monkeypatch.setattr(
        langfuse_tracing,
        "propagate_attributes",
        _fake_propagate_attributes,
    )

    with langfuse_tracing.langfuse_trace_context(
        trace_name="queue.summarize",
        user_id=42,
        session_id="worker-1",
        metadata={"source": "queue", "task_id": 7, "data": {"x": 1}},
        tags=["queue", "", "summarize"],
    ):
        pass

    assert captured["kwargs"] == {
        "trace_name": "queue.summarize",
        "user_id": "42",
        "session_id": "worker-1",
        "metadata": {
            "source": "queue",
            "task_id": "7",
            "data": '{"x": 1}',
        },
        "tags": ["queue", "summarize"],
    }


def test_extract_google_usage_details() -> None:
    usage = SimpleNamespace(
        prompt_token_count=11,
        candidates_token_count=7,
        total_token_count=18,
    )
    response = SimpleNamespace(usage_metadata=usage)

    details = langfuse_tracing.extract_google_usage_details(response)

    assert details == {"input": 11, "output": 7, "total": 18}


def test_extract_google_usage_details_preserves_zero_counts() -> None:
    usage = SimpleNamespace(
        prompt_token_count=0,
        input_token_count=99,
        candidates_token_count=0,
        output_token_count=77,
        total_token_count=0,
    )
    response = SimpleNamespace(usage_metadata=usage)

    details = langfuse_tracing.extract_google_usage_details(response)

    assert details == {"input": 0, "output": 0, "total": 0}


def test_langfuse_generation_context_noop_when_disabled() -> None:
    _reset_langfuse_state()
    langfuse_tracing._LANGFUSE_INITIALIZED = True
    langfuse_tracing._LANGFUSE_READY = False

    with langfuse_tracing.langfuse_generation_context(name="x", model="y") as generation:
        assert generation is None


def test_initialize_langfuse_tracing_does_not_retry_after_missing_keys(monkeypatch) -> None:
    _reset_langfuse_state()
    state = {"calls": 0}

    def _get_settings() -> SimpleNamespace:
        state["calls"] += 1
        if state["calls"] == 1:
            return _settings(langfuse_public_key=None, langfuse_secret_key=None)
        return _settings()

    captured: dict[str, object] = {"instrumentation_calls": 0, "langfuse_inits": 0}

    class FakeLangfuse:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs
            captured["langfuse_inits"] = cast(int, captured["langfuse_inits"]) + 1

        def flush(self) -> None:
            return None

    def _instrument_all(_: InstrumentationSettings) -> None:
        captured["instrumentation_calls"] = cast(int, captured["instrumentation_calls"]) + 1

    monkeypatch.setattr(langfuse_tracing, "get_settings", _get_settings)
    monkeypatch.setattr(langfuse_tracing, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(langfuse_tracing.Agent, "instrument_all", _instrument_all)

    assert langfuse_tracing.initialize_langfuse_tracing() is False
    assert langfuse_tracing._LANGFUSE_INITIALIZED is True
    assert langfuse_tracing._LANGFUSE_READY is False
    assert langfuse_tracing.initialize_langfuse_tracing() is False
    assert captured["langfuse_inits"] == 0
    assert captured["instrumentation_calls"] == 0


def test_flush_langfuse_tracing_calls_client_flush() -> None:
    _reset_langfuse_state()
    captured: dict[str, int] = {"flush_calls": 0}

    class FakeLangfuse:
        def flush(self) -> None:
            captured["flush_calls"] += 1

    langfuse_tracing._LANGFUSE_READY = True
    langfuse_tracing._LANGFUSE_CLIENT = FakeLangfuse()

    langfuse_tracing.flush_langfuse_tracing()

    assert captured["flush_calls"] == 1


def test_langfuse_generation_context_yields_generation(monkeypatch) -> None:
    _reset_langfuse_state()
    langfuse_tracing._LANGFUSE_INITIALIZED = True
    langfuse_tracing._LANGFUSE_READY = True
    captured: dict[str, object] = {}

    class FakeClient:
        @contextmanager
        def start_as_current_observation(self, **kwargs: object):
            captured["kwargs"] = kwargs
            yield "fake-generation"

    langfuse_tracing._LANGFUSE_CLIENT = FakeClient()

    with langfuse_tracing.langfuse_generation_context(
        name="llm.call",
        model="gpt-5.6-luna",
        input_data={"prompt": "hi"},
        metadata={"attempt": 2},
    ) as generation:
        assert generation == "fake-generation"

    assert captured["kwargs"] == {
        "name": "llm.call",
        "as_type": "generation",
        "model": "gpt-5.6-luna",
        "input": {"prompt": "hi"},
        "metadata": {"attempt": "2"},
    }
