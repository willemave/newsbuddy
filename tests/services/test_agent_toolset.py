from __future__ import annotations

from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, cast

from app.services.agent_toolset import (
    AgentToolPolicy,
    AgentToolsetConfig,
    register_agent_vm_tools,
)
from app.services.agent_vm_runtime import resolve_workspace_relative_path


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
        def resolve_relative_path(self, path: str) -> str:
            return path

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


def test_file_tools_normalize_workspace_absolute_paths_and_report_rejections() -> None:
    agent = _FakeAgent()
    events: list[tuple[str, dict[str, Any]]] = []
    workspace_root = PurePosixPath("/tmp/newsly/tasks/55")

    class Session:
        def __init__(self) -> None:
            self.files: dict[str, str] = {}

        def resolve_relative_path(self, path: str) -> str:
            return resolve_workspace_relative_path(
                path,
                workspace_root=workspace_root,
            ).as_posix()

        def write_file(self, path: str, text: str) -> None:
            self.files[path] = text

        def read_file(self, path: str, *, max_bytes: int) -> str:
            del max_bytes
            return self.files[path]

        def list_files(self, path: str = ".") -> list[str]:
            prefix = "" if path == "." else f"{path.rstrip('/')}/"
            return sorted(candidate for candidate in self.files if candidate.startswith(prefix))

    session = Session()
    register_agent_vm_tools(
        cast(Any, agent),
        session_getter=lambda _deps: cast(Any, session),
        log_event=lambda _deps, event, payload: events.append((event, payload)),
        user_id_getter=lambda _deps: None,
        metadata_getter=lambda _deps: {},
        config=AgentToolsetConfig(feature="test", operation_prefix="test", source="unit"),
    )
    ctx = SimpleNamespace(deps=object())
    absolute_path = "/tmp/newsly/tasks/55/output/source-notes.md"

    write_result = agent.tools["write_file"](ctx, absolute_path, "notes")
    read_result = agent.tools["read_file"](ctx, absolute_path)
    list_result = agent.tools["list_files"](ctx, "/tmp/newsly/tasks/55/output")
    rejected_result = agent.tools["write_file"](ctx, "/etc/passwd", "blocked")
    rejected_read = agent.tools["read_file"](ctx, "/etc/passwd")
    rejected_list = agent.tools["list_files"](ctx, "/etc")

    assert write_result == {
        "ok": True,
        "path": "output/source-notes.md",
        "chars": 5,
    }
    assert read_result == {
        "ok": True,
        "path": "output/source-notes.md",
        "text": "notes",
    }
    assert list_result == {
        "ok": True,
        "path": "output",
        "files": ["output/source-notes.md"],
    }
    expected_rejection = {
        "ok": False,
        "error": (
            "VM path is outside the task workspace. Address files with workspace-relative paths."
        ),
    }
    assert rejected_result == expected_rejection
    assert rejected_read == expected_rejection
    assert rejected_list == expected_rejection
    assert "/etc" not in str(expected_rejection)
    assert session.files == {"output/source-notes.md": "notes"}
    assert events[0] == (
        "write_file",
        {
            "path": "output/source-notes.md",
            "requested_path": absolute_path,
            "chars": 5,
        },
    )
    assert [event for event, _payload in events[-3:]] == [
        "write_file_failed",
        "read_file_failed",
        "list_files_failed",
    ]
    assert [payload["requested_path"] for _event, payload in events[-3:]] == [
        "/etc/passwd",
        "/etc/passwd",
        "/etc",
    ]


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
