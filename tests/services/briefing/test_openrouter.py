from __future__ import annotations

from types import SimpleNamespace

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
