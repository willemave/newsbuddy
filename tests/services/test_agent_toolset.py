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


def test_register_agent_vm_tools_exposes_one_five_tool_vm_surface() -> None:
    agent = _FakeAgent()

    register_agent_vm_tools(
        cast(Any, agent),
        session_getter=lambda _deps: cast(Any, None),
        log_event=lambda _deps, _event, _payload: None,
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
        "edit_file",
        "read_file",
        "list_files",
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
            self.sandbox_id = None

        def resolve_relative_path(self, path: str) -> str:
            return resolve_workspace_relative_path(
                path,
                workspace_root=workspace_root,
            ).as_posix()

        def write_file(self, path: str, text: str) -> None:
            self.files[path] = text

        def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
            del max_bytes
            return self.files[self.resolve_relative_path(path)]

        def list_files(self, path: str = ".") -> list[str]:
            resolved = self.resolve_relative_path(path)
            prefix = "" if resolved == "." else f"{resolved.rstrip('/')}/"
            return sorted(candidate for candidate in self.files if candidate.startswith(prefix))

    session = Session()
    register_agent_vm_tools(
        cast(Any, agent),
        session_getter=lambda _deps: cast(Any, session),
        log_event=lambda _deps, event, payload: events.append((event, payload)),
        config=AgentToolsetConfig(feature="test", operation_prefix="test", source="unit"),
    )
    ctx = SimpleNamespace(deps=object())
    absolute_path = "/tmp/newsly/tasks/55/output/source-notes.md"

    write_result = agent.tools["write_file"](ctx, absolute_path, "notes")
    edit_result = agent.tools["edit_file"](ctx, absolute_path, "notes", "revised notes")
    read_result = agent.tools["read_file"](ctx, absolute_path)
    list_result = agent.tools["list_files"](ctx, "/tmp/newsly/tasks/55/output")
    rejected_result = agent.tools["write_file"](ctx, "/etc/passwd", "blocked")
    rejected_edit = agent.tools["edit_file"](ctx, "/etc/passwd", "root", "blocked")
    rejected_read = agent.tools["read_file"](ctx, "/etc/passwd")
    rejected_list = agent.tools["list_files"](ctx, "/etc")

    assert write_result == {
        "ok": True,
        "path": "output/source-notes.md",
        "chars": 5,
    }
    assert edit_result == {
        "ok": True,
        "path": "output/source-notes.md",
        "replacements": 1,
        "chars": 13,
    }
    assert read_result == {
        "ok": True,
        "path": absolute_path,
        "text": "revised notes",
    }
    assert list_result == {
        "ok": True,
        "path": "/tmp/newsly/tasks/55/output",
        "files": ["output/source-notes.md"],
    }
    expected_rejection = {
        "ok": False,
        "error": (
            "VM path is outside the task workspace. Address files with workspace-relative paths."
        ),
    }
    assert rejected_result == expected_rejection
    assert rejected_edit == expected_rejection
    assert rejected_read == expected_rejection
    assert rejected_list == expected_rejection
    assert "/etc" not in str(expected_rejection)
    assert session.files == {"output/source-notes.md": "revised notes"}
    assert events[0][0] == "write_file"
    assert events[0][1]["path"] == "output/source-notes.md"
    assert events[0][1]["requested_path"] == absolute_path
    assert events[0][1]["chars"] == 5
    assert events[0][1]["sandbox_id"] is None
    assert [event for event, _payload in events[-4:]] == [
        "write_file_failed",
        "edit_file_failed",
        "read_file_failed",
        "list_files_failed",
    ]
    assert [payload["requested_path"] for _event, payload in events[-4:]] == [
        "/etc/passwd",
        "/etc/passwd",
        "/etc/passwd",
        "/etc",
    ]


def test_edit_file_requires_a_unique_match_unless_replace_all_is_set() -> None:
    agent = _FakeAgent()
    events: list[tuple[str, dict[str, Any]]] = []

    class Session:
        sandbox_id = "sandbox-test"

        def __init__(self) -> None:
            self.text = "alpha beta alpha"
            self.write_calls = 0

        def resolve_relative_path(self, path: str) -> str:
            return path

        def read_file(self, _path: str, *, max_bytes: int | None = None) -> str:
            del max_bytes
            return self.text

        def write_file(self, _path: str, text: str) -> None:
            self.text = text
            self.write_calls += 1

    session = Session()
    register_agent_vm_tools(
        cast(Any, agent),
        session_getter=lambda _deps: cast(Any, session),
        log_event=lambda _deps, event, payload: events.append((event, payload)),
        config=AgentToolsetConfig(feature="test", operation_prefix="test", source="unit"),
    )
    ctx = SimpleNamespace(deps=object())

    ambiguous = agent.tools["edit_file"](ctx, "notes.txt", "alpha", "omega")
    replaced = agent.tools["edit_file"](
        ctx,
        "notes.txt",
        "alpha",
        "omega",
        replace_all=True,
    )

    assert ambiguous == {
        "ok": False,
        "path": "notes.txt",
        "error": "old_text occurs 2 times; provide more context or set replace_all",
        "occurrences": 2,
    }
    assert replaced == {
        "ok": True,
        "path": "notes.txt",
        "replacements": 2,
        "chars": 16,
    }
    assert session.text == "omega beta omega"
    assert session.write_calls == 1
    assert [event for event, _payload in events] == ["edit_file_failed", "edit_file"]


def test_edit_file_rejects_empty_or_missing_old_text_without_writing() -> None:
    agent = _FakeAgent()

    class Session:
        sandbox_id = None

        def __init__(self) -> None:
            self.write_calls = 0

        def resolve_relative_path(self, path: str) -> str:
            return path

        def read_file(self, _path: str, *, max_bytes: int | None = None) -> str:
            del max_bytes
            return "current contents"

        def write_file(self, _path: str, _text: str) -> None:
            self.write_calls += 1

    session = Session()
    register_agent_vm_tools(
        cast(Any, agent),
        session_getter=lambda _deps: cast(Any, session),
        log_event=lambda _deps, _event, _payload: None,
        config=AgentToolsetConfig(feature="test", operation_prefix="test", source="unit"),
    )
    ctx = SimpleNamespace(deps=object())

    empty = agent.tools["edit_file"](ctx, "notes.txt", "", "replacement")
    missing = agent.tools["edit_file"](ctx, "notes.txt", "absent", "replacement")

    assert empty == {
        "ok": False,
        "path": "notes.txt",
        "error": "old_text must not be empty",
    }
    assert missing == {
        "ok": False,
        "path": "notes.txt",
        "error": "old_text was not found",
        "occurrences": 0,
    }
    assert session.write_calls == 0


def test_execute_bash_always_passes_a_bounded_timeout() -> None:
    agent = _FakeAgent()
    timeouts: list[int | None] = []
    events: list[dict[str, Any]] = []

    class Session:
        sandbox_id = "sandbox-test"
        lease = SimpleNamespace(reused=False)

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
    completed_events = [event for event in events if "requested_timeout_seconds" in event]
    assert [event["timeout_seconds"] for event in completed_events] == [45, 90, 1]
    assert [event["requested_timeout_seconds"] for event in completed_events] == [None, 900, 0]
