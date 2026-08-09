from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from app.services.agent_toolset import (
    AgentToolPolicy,
    AgentToolsetConfig,
    register_agent_vm_tools,
)


class _FakeAgent:
    def __init__(self) -> None:
        self.tool_names: list[str] = []
        self.tools: dict[str, Any] = {}

    def tool(self, func):
        self.tool_names.append(func.__name__)
        self.tools[func.__name__] = func
        return func


def test_register_agent_vm_tools_respects_tool_policy() -> None:
    agent = _FakeAgent()

    register_agent_vm_tools(
        cast(Any, agent),
        session_getter=lambda _deps: cast(Any, None),
        log_event=lambda _deps, _event, _payload: None,
        user_id_getter=lambda _deps: None,
        metadata_getter=lambda _deps: {},
        config=AgentToolsetConfig(
            feature="test",
            operation_prefix="test",
            source="unit",
            tool_policy=AgentToolPolicy.from_mapping(
                {
                    "execute_bash": False,
                    "web_search": False,
                    "files": "read_only",
                }
            ),
        ),
    )

    assert agent.tool_names == ["read_file", "list_files"]


def test_register_agent_vm_tools_exposes_one_five_tool_surface() -> None:
    agent = _FakeAgent()

    register_agent_vm_tools(
        cast(Any, agent),
        session_getter=lambda _deps: cast(Any, None),
        log_event=lambda _deps, _event, _payload: None,
        user_id_getter=lambda _deps: None,
        metadata_getter=lambda _deps: {},
        config=AgentToolsetConfig(
            feature="test",
            operation_prefix="test",
            source="unit",
            tool_policy=AgentToolPolicy.from_mapping(
                {
                    "execute_bash": True,
                    "web_search": True,
                    "files": "read_write",
                }
            ),
        ),
    )

    assert agent.tool_names == [
        "execute_bash",
        "write_file",
        "read_file",
        "list_files",
        "web_search",
    ]


def test_read_file_returns_typed_failure_for_missing_artifact() -> None:
    agent = _FakeAgent()

    class Session:
        def read_file(self, _path: str, *, max_bytes: int) -> str:
            del max_bytes
            raise FileNotFoundError("missing")

    register_agent_vm_tools(
        cast(Any, agent),
        session_getter=lambda _deps: cast(Any, Session()),
        log_event=lambda _deps, _event, _payload: None,
        user_id_getter=lambda _deps: None,
        metadata_getter=lambda _deps: {},
        config=AgentToolsetConfig(feature="test", operation_prefix="test", source="unit"),
    )

    result = agent.tools["read_file"](
        SimpleNamespace(deps=object()),
        "output/index.html",
    )

    assert result == {
        "ok": False,
        "path": "output/index.html",
        "error": "File not found or unreadable: output/index.html",
    }


def test_execute_bash_always_passes_a_bounded_timeout() -> None:
    agent = _FakeAgent()
    timeouts: list[int | None] = []
    events: list[dict[str, Any]] = []

    class Session:
        def execute_bash(
            self,
            _command: str,
            *,
            timeout_seconds: int | None = None,
        ) -> SimpleNamespace:
            timeouts.append(timeout_seconds)
            return SimpleNamespace(exit_code=0, stdout="ok", stderr="")

    register_agent_vm_tools(
        cast(Any, agent),
        session_getter=lambda _deps: cast(Any, Session()),
        log_event=lambda _deps, _event, payload: events.append(payload),
        user_id_getter=lambda _deps: None,
        metadata_getter=lambda _deps: {},
        config=AgentToolsetConfig(
            feature="test",
            operation_prefix="test",
            source="unit",
            default_bash_timeout_seconds=45,
            max_bash_timeout_seconds=90,
        ),
    )
    execute_bash = agent.tools["execute_bash"]
    ctx = SimpleNamespace(deps=object())

    execute_bash(ctx, "first")
    execute_bash(ctx, "second", timeout_seconds=900)
    execute_bash(ctx, "third", timeout_seconds=0)

    assert timeouts == [45, 90, 1]
    assert [event["timeout_seconds"] for event in events] == [45, 90, 1]
    assert [event["requested_timeout_seconds"] for event in events] == [None, 900, 0]
