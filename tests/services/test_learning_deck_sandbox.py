from __future__ import annotations

import sys
from types import SimpleNamespace

from e2b.sandbox.commands.command_handle import CommandExitException

from app.core.settings import get_settings
from app.services.learning_deck_agent import (
    INPUT_DESIGN_BRIEF,
    _build_agent_prompt,
    _prepare_sandbox_inputs,
)
from app.services.learning_deck_sandbox import (
    E2BLearningDeckSandboxSession,
    LocalLearningDeckSandboxSession,
)


def test_e2b_sandbox_uses_configured_workdir_as_command_cwd(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost/test_db")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.setenv("LEARNING_SANDBOX_E2B_API_KEY", "test-e2b-key")
    monkeypatch.setenv("LEARNING_SANDBOX_WORKDIR", "/tmp/newsly-test-deck")
    get_settings.cache_clear()

    created: list[tuple[dict, FakeSandbox]] = []

    class FakeCommands:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def run(self, command: str, **kwargs):
            self.calls.append((command, kwargs))
            if command == "bad command":
                raise CommandExitException(
                    stderr="bad stderr",
                    stdout="bad stdout",
                    exit_code=2,
                    error="bad",
                )
            return SimpleNamespace(stdout="", stderr="", exit_code=0)

    class FakeFiles:
        def __init__(self) -> None:
            self.writes: list[tuple[str, str]] = []

        def write(self, path: str, text: str) -> None:
            self.writes.append((path, text))

    class FakeSandbox:
        sandbox_id = "sandbox-test"

        def __init__(self) -> None:
            self.commands = FakeCommands()
            self.files = FakeFiles()

        @classmethod
        def create(cls, **kwargs):
            sandbox = cls()
            created.append((kwargs, sandbox))
            return sandbox

        def kill(self) -> None:
            return

    monkeypatch.setitem(
        sys.modules,
        "e2b_code_interpreter",
        SimpleNamespace(Sandbox=FakeSandbox),
    )
    monkeypatch.setattr(
        "app.services.learning_deck_sandbox.record_vendor_usage_out_of_band",
        lambda **_kwargs: None,
    )

    try:
        session = E2BLearningDeckSandboxSession(user_id=1, run_id=2)
        session.run_command("pwd", timeout_seconds=12)
        failed = session.run_command("bad command")
        session.write_file("input/source-snapshot.json", "{}")
    finally:
        get_settings.cache_clear()

    assert created
    _, sandbox = created[0]
    assert sandbox.commands.calls[0] == ("mkdir -p /tmp/newsly-test-deck", {"timeout": None})
    assert sandbox.commands.calls[1] == (
        "pwd",
        {"cwd": "/tmp/newsly-test-deck", "timeout": 12},
    )
    assert sandbox.commands.calls[2] == (
        "bad command",
        {"cwd": "/tmp/newsly-test-deck", "timeout": None},
    )
    assert failed.exit_code == 2
    assert failed.stdout == "bad stdout"
    assert failed.stderr == "bad stderr"
    assert sandbox.commands.calls[3] == (
        "mkdir -p /tmp/newsly-test-deck/input",
        {"timeout": None},
    )
    assert sandbox.files.writes == [("/tmp/newsly-test-deck/input/source-snapshot.json", "{}")]


def test_prepare_sandbox_inputs_writes_source_text_placeholder_for_metadata_only_source() -> None:
    sandbox = LocalLearningDeckSandboxSession.create()

    _prepare_sandbox_inputs(
        sandbox,
        source_snapshot={
            "source_kind": "github_repo",
            "source_url": "https://github.com/octocat/Hello-World",
            "source_title": "octocat/Hello-World",
        },
        interests_prompt=None,
    )

    assert "No primary source text was provided" in sandbox.read_file("input/source.txt")
    design_brief = sandbox.read_file(INPUT_DESIGN_BRIEF)
    assert "Build the deck like a strong technical conference talk" in design_brief
    assert "Each major section needs a visual anchor" in design_brief
    assert "source-snapshot.json" in "\n".join(sandbox.list_files("input"))


def test_agent_prompt_requires_design_brief_verification() -> None:
    prompt = _build_agent_prompt(
        {
            "source_kind": "github_repo",
            "source_url": "https://github.com/openai/codex",
            "source_title": "openai/codex",
        },
        interests_prompt="Explain architecture",
    )

    assert "Design brief: input/deck-design-brief.md" in prompt
    assert "Daylight house classes" in prompt
    assert "source-specific graphics" in prompt


def test_agent_prompt_treats_github_blob_as_repo_plus_raw_artifact() -> None:
    prompt = _build_agent_prompt(
        {
            "source_kind": "github_repo",
            "source_url": "https://github.com/deepseek-ai/DeepSpec/blob/main/DSpark_paper.pdf",
            "source_title": "deepseek-ai/DeepSpec: DSpark_paper.pdf",
            "source_metadata": {
                "owner": "deepseek-ai",
                "repo": "DeepSpec",
                "linked_artifact": {
                    "path": "DSpark_paper.pdf",
                    "ref": "main",
                    "raw_url": (
                        "https://raw.githubusercontent.com/deepseek-ai/DeepSpec/main/"
                        "DSpark_paper.pdf"
                    ),
                },
            },
        },
        interests_prompt=None,
    )

    assert "treat the request as research over the repository" in prompt
    assert "download/read the raw linked artifact" in prompt
    assert "Do not treat the GitHub HTML blob page" in prompt
    assert "Linked artifact path: DSpark_paper.pdf" in prompt
