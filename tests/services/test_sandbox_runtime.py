"""Deterministic contract tests for the chat personal-library sandbox."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from app.core.settings import Settings, get_settings
from app.services import sandbox_runtime


class _FakeFiles:
    def __init__(self, sandbox: _FakeSandbox) -> None:
        self._sandbox = sandbox

    def write(self, path: str, text: str) -> None:
        self._sandbox.contents[path] = text

    def read(self, path: str) -> str:
        return self._sandbox.contents[path]


class _FakeCommands:
    def __init__(self, sandbox: _FakeSandbox) -> None:
        self._sandbox = sandbox

    def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> SimpleNamespace:
        self._sandbox.commands_run.append((command, cwd, timeout))
        if self._sandbox.fail_initialization and command.startswith("mkdir -p"):
            raise RuntimeError("workspace bootstrap failed")
        if "find . -type f -name '*.md'" in command:
            root = "/tmp/newsly/personal_markdown/"
            paths = sorted(
                f"./{path.removeprefix(root)}"
                for path in self._sandbox.contents
                if path.startswith(root) and path.endswith(".md")
            )
            return SimpleNamespace(stdout="\n".join(paths), stderr="", exit_code=0)
        if command.startswith("python3 - <<'PY'"):
            return SimpleNamespace(
                stdout="topics/e2b.md:1:E2B sandbox research",
                stderr="",
                exit_code=0,
            )
        return SimpleNamespace(stdout="", stderr="", exit_code=0)


class _FakeSandbox:
    created: list[_FakeSandbox] = []
    fail_next_initialization = False

    def __init__(self, *, fail_initialization: bool = False) -> None:
        self.fail_initialization = fail_initialization
        self.contents: dict[str, str] = {}
        self.commands_run: list[tuple[str, str | None, int | None]] = []
        self.killed = False
        self.commands = _FakeCommands(self)
        self.files = _FakeFiles(self)

    @classmethod
    def create(cls, **kwargs) -> _FakeSandbox:  # noqa: ANN003
        sandbox = cls(fail_initialization=cls.fail_next_initialization)
        sandbox.create_kwargs = kwargs
        cls.created.append(sandbox)
        cls.fail_next_initialization = False
        return sandbox

    def kill(self) -> None:
        self.killed = True


@pytest.fixture(autouse=True)
def _fake_e2b_module(monkeypatch):
    _FakeSandbox.created = []
    _FakeSandbox.fail_next_initialization = False
    module = ModuleType("e2b_code_interpreter")
    module.Sandbox = _FakeSandbox
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", module)


def _configure_e2b(monkeypatch, *, library_root: Path) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "chat_sandbox_provider", "e2b")
    monkeypatch.setattr(settings, "chat_sandbox_e2b_api_key", "test-e2b-key")
    monkeypatch.setattr(settings, "chat_sandbox_template", "chat-template")
    monkeypatch.setattr(settings, "chat_sandbox_library_root", "/tmp/newsly/personal_markdown")
    monkeypatch.setattr(
        sandbox_runtime,
        "get_personal_markdown_user_root",
        lambda _user_id: library_root,
    )


def test_chat_sandbox_defaults_to_e2b() -> None:
    assert Settings.model_fields["chat_sandbox_provider"].default == "e2b"
    assert (
        Settings.model_fields["chat_sandbox_library_root"].default
        == "/tmp/newsly/personal_markdown"
    )


def test_local_chat_search_treats_leading_option_as_literal_pattern(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    monkeypatch.setattr(sandbox_runtime.shutil, "which", lambda _command: "/usr/bin/rg")
    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fake_run)
    session = sandbox_runtime.LocalPersonalLibrarySandboxSession(library_root=tmp_path)

    session.search_files(query="--files")

    assert captured["args"][-3:] == ["-e", "--files", "."]


def test_e2b_chat_sandbox_hydrates_and_exposes_library_tools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    library_root = tmp_path / "personal-markdown" / "7"
    topic_dir = library_root / "topics"
    topic_dir.mkdir(parents=True)
    (topic_dir / "e2b.md").write_text("# E2B\n\nSandbox research", encoding="utf-8")
    _configure_e2b(monkeypatch, library_root=library_root)
    usage_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        sandbox_runtime,
        "record_vendor_usage_out_of_band",
        lambda **kwargs: usage_calls.append(kwargs),
    )

    session = sandbox_runtime.create_personal_library_sandbox_session(user_id=7)

    assert isinstance(session, sandbox_runtime.E2BPersonalLibrarySandboxSession)
    sandbox = _FakeSandbox.created[0]
    assert sandbox.create_kwargs["api_key"] == "test-e2b-key"
    assert sandbox.create_kwargs["template"] == "chat-template"
    assert sandbox.contents["/tmp/newsly/personal_markdown/topics/e2b.md"].startswith("# E2B")
    assert "./topics/e2b.md" in session.list_files()
    assert session.read_file(relative_path="topics/e2b.md").startswith("# E2B")
    assert "topics/e2b.md:1" in session.search_files(query="sandbox research")
    assert usage_calls[0]["feature"] == "chat_sandbox"

    session.close()

    assert sandbox.killed is True


def test_e2b_chat_sandbox_kills_failed_initialization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure_e2b(monkeypatch, library_root=tmp_path)
    monkeypatch.setattr(sandbox_runtime, "record_vendor_usage_out_of_band", lambda **_kwargs: None)
    _FakeSandbox.fail_next_initialization = True

    with pytest.raises(
        sandbox_runtime.SandboxRuntimeUnavailableError,
        match="Unable to initialize E2B chat sandbox",
    ):
        sandbox_runtime.create_personal_library_sandbox_session(user_id=7)

    assert _FakeSandbox.created[0].killed is True
