from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.settings import get_settings
from app.services.briefing import openrouter


def test_structured_request_uses_schema_and_returns_usage(monkeypatch) -> None:
    captured: dict[str, object] = {}
    clients = []

    class FakeCompletions:
        def create(self, **kwargs):  # noqa: ANN003, ANN201
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"value": 1}'))],
                usage=SimpleNamespace(
                    prompt_tokens=12,
                    completion_tokens=4,
                    total_tokens=16,
                ),
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):  # noqa: ANN003
            self.chat = SimpleNamespace(completions=FakeCompletions())
            self.closed = False
            clients.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(openrouter, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(openrouter.httpx, "Client", lambda **_kwargs: object())
    settings = get_settings().model_copy(update={"openrouter_api_key": "test-key"})

    result = openrouter.request_openrouter_json_schema(
        model_spec="openrouter:test/model",
        system_prompt="system",
        user_prompt="user",
        schema_name="TestOutput",
        schema={"type": "object"},
        timeout_seconds=30,
        settings=settings,
    )

    assert captured["model"] == "test/model"
    assert captured["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "TestOutput",
            "strict": True,
            "schema": {"type": "object"},
        },
    }
    assert result.content == '{"value": 1}'
    assert clients[0].closed is True
    assert result.usage == {
        "input_tokens": 12,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "output_tokens": 4,
        "total_tokens": 16,
    }


def test_strip_json_code_fence() -> None:
    assert openrouter.strip_json_code_fence('```json\n{"value": 1}\n```') == '{"value": 1}'
    assert openrouter.strip_json_code_fence('{"value": 1}') == '{"value": 1}'


def test_refresh_scoped_client_reuses_pool_and_recovers_after_failed_call(monkeypatch) -> None:
    clients = []

    class FlakyCompletions:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **_kwargs):  # noqa: ANN003, ANN201
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary provider failure")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"value": 2}'))],
                usage=None,
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):  # noqa: ANN003
            self.completions = FlakyCompletions()
            self.chat = SimpleNamespace(completions=self.completions)
            self.closed = False
            clients.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(openrouter, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(openrouter.httpx, "Client", lambda **_kwargs: object())
    settings = get_settings().model_copy(update={"openrouter_api_key": "test-key"})

    with openrouter.BriefingOpenRouterClient(
        timeout_seconds=30,
        settings=settings,
    ) as client:
        with pytest.raises(RuntimeError, match="temporary provider failure"):
            client.request_json_schema(
                model_spec="openrouter:test/model",
                system_prompt="system",
                user_prompt="first",
                schema_name="TestOutput",
                schema={"type": "object"},
            )
        result = client.request_json_schema(
            model_spec="openrouter:test/model",
            system_prompt="system",
            user_prompt="second",
            schema_name="TestOutput",
            schema={"type": "object"},
        )

    assert result.content == '{"value": 2}'
    assert len(clients) == 1
    assert clients[0].completions.calls == 2
    assert clients[0].closed is True


def test_refresh_scoped_client_does_not_create_pool_until_first_request(monkeypatch) -> None:
    created = 0

    def create_http_client(**_kwargs):  # noqa: ANN003, ANN202
        nonlocal created
        created += 1
        return object()

    monkeypatch.setattr(openrouter.httpx, "Client", create_http_client)
    settings = get_settings().model_copy(update={"openrouter_api_key": None})

    with openrouter.BriefingOpenRouterClient(timeout_seconds=30, settings=settings):
        pass

    assert created == 0


def test_refresh_scoped_client_closes_http_pool_when_openai_init_fails(monkeypatch) -> None:
    class FakeHTTPClient:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    http_client = FakeHTTPClient()
    monkeypatch.setattr(openrouter.httpx, "Client", lambda **_kwargs: http_client)

    def fail_openai(**_kwargs):  # noqa: ANN003, ANN202
        raise RuntimeError("client init failed")

    monkeypatch.setattr(openrouter, "OpenAI", fail_openai)
    settings = get_settings().model_copy(update={"openrouter_api_key": "test-key"})

    with (
        openrouter.BriefingOpenRouterClient(timeout_seconds=30, settings=settings) as client,
        pytest.raises(RuntimeError, match="client init failed"),
    ):
        client.request_json_schema(
            model_spec="openrouter:test/model",
            system_prompt="system",
            user_prompt="user",
            schema_name="TestOutput",
            schema={"type": "object"},
        )

    assert http_client.closed is True
