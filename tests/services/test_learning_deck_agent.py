from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from app.models.db import VendorUsageRecord
from app.services import learning_deck_agent
from app.services.learning_deck_sandbox import LearningDeckSandboxSession


class _FakeAgentResult:
    output = "Deck generated."

    def usage(self) -> object:
        return SimpleNamespace(input_tokens=1000, output_tokens=500, total_tokens=1500)


class _FakeAgent:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def tool(self, func):
        return func

    def run_sync(self, *_args: Any, **_kwargs: Any) -> _FakeAgentResult:
        return _FakeAgentResult()


class _FakeSandbox:
    provider = "local"
    sandbox_id = "sandbox-usage"

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.closed = False

    def run_command(
        self,
        _command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> SimpleNamespace:
        del timeout_seconds
        return SimpleNamespace(exit_code=0, stdout="", stderr="")

    def write_file(self, path: str, text: str) -> None:
        self.files[path] = text

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        del max_bytes
        if path == learning_deck_agent.OUTPUT_INDEX_HTML:
            return (
                "<html><body><div class='reveal'><div class='slides'>"
                "<section>Deck</section></div></div></body></html>"
            )
        if path == learning_deck_agent.OUTPUT_SOURCE_NOTES:
            return "# Source Notes\n\n## Sources\n\n- Primary source."
        if path == learning_deck_agent.OUTPUT_SOURCE_METADATA:
            return "{}"
        return self.files[path]

    def read_file_bytes(self, _path: str, *, max_bytes: int | None = None) -> bytes:
        del max_bytes
        return b""

    def list_files(self, _path: str = ".") -> list[str]:
        return []

    def close(self) -> None:
        self.closed = True


def test_learning_deck_agent_persists_vendor_usage_row(
    db_session,
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    sandbox = _FakeSandbox()
    monkeypatch.setattr(learning_deck_agent, "Agent", _FakeAgent)
    monkeypatch.setattr(
        learning_deck_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )
    monkeypatch.setattr(learning_deck_agent, "resolve_model_provider", lambda _model_spec: "openai")

    result = learning_deck_agent.run_learning_deck_agent(
        source_snapshot={
            "source_kind": "content",
            "source_identity": "content:77",
            "source_content_id": 77,
            "source_title": "Deck Source",
            "body_text": "Source body for a generated learning deck.",
        },
        interests_prompt="Focus on systems",
        user_id=test_user.id,
        run_id=123,
        sandbox_factory=lambda _user_id, _run_id: cast(LearningDeckSandboxSession, sandbox),
    )

    assert result.model_provider == "openai"
    assert sandbox.closed is True
    row = (
        db_session.query(VendorUsageRecord)
        .filter(VendorUsageRecord.feature == "learning_deck_generation")
        .one()
    )
    assert row.operation == "learning_deck.generate"
    assert row.source == "queue"
    assert row.user_id == test_user.id
    assert row.content_id == 77
    assert row.input_tokens == 1000
    assert row.output_tokens == 500
    assert row.total_tokens == 1500
    assert row.metadata_json == {
        "run_id": 123,
        "source_kind": "content",
        "source_identity": "content:77",
        "source_content_id": 77,
    }
