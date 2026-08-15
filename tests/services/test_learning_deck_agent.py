from __future__ import annotations

import json
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.models.contracts import LlmTaskKind, LlmTaskMode
from app.models.db import VendorUsageRecord
from app.services import learning_deck_agent, learning_deck_browser_validation
from app.services.agent_vm_runtime import (
    AgentVmDeadlineExceeded,
    resolve_workspace_relative_path,
)
from app.services.learning_deck_agent import LearningDeckAgentExecutionError
from app.services.learning_deck_theme import DECK_THEME_STYLE_ID
from app.services.llm_tasks import create_llm_task


def _valid_deck_html(body: str = "Deck") -> str:
    return (
        "<html><head><meta name='newsly-deck-layout' content='responsive-v2'>"
        "<style>.reveal .slides section { color: #eee; background: #111; "
        "padding: 2rem; }</style><body><div class='reveal'><div class='slides'>"
        f"<section>{body}</section></div></div></body></html>"
    )


def _browser_validation_outcome() -> dict[str, Any]:
    orientation_common = {
        "slides_checked": 2,
        "overflow_slides": [],
        "vertical_occupancy": {"minimum": 0.4, "maximum": 0.8},
    }
    return {
        "status": "passed",
        "validator": "playwright_chromium",
        "responsive_layout": "responsive-v2",
        "reveal_ready": True,
        "current_slide_exists": True,
        "slide_count": 2,
        "navigation": "next_previous_round_trip",
        "initial_indices": {"h": 0, "v": 0, "f": -1},
        "next_indices": {"h": 1, "v": 0, "f": -1},
        "previous_indices": {"h": 0, "v": 0, "f": -1},
        "relevant_asset_loads": 2,
        "portrait": {
            "viewport": {"width": 390, "height": 844},
            "canvas": {"width": 720, "height": 1280},
            **orientation_common,
        },
        "landscape": {
            "viewport": {"width": 844, "height": 390},
            "canvas": {"width": 1280, "height": 720},
            **orientation_common,
        },
    }


class _FakeAgentResult:
    output = "Deck generated."

    @property
    def usage(self) -> object:
        return SimpleNamespace(input_tokens=1000, output_tokens=500, total_tokens=1500)


class _FakeAgent:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def tool(self, func):
        return func

    def run_sync(self, *_args: Any, **_kwargs: Any) -> _FakeAgentResult:
        return _FakeAgentResult()


class _DeadlineAgent(_FakeAgent):
    def run_sync(self, *_args: Any, **_kwargs: Any) -> _FakeAgentResult:
        raise AgentVmDeadlineExceeded("agent run expired")


class _FakeSandbox:
    provider = "local"
    sandbox_id = "sandbox-usage"

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.closed = False
        self.commands: list[str] = []
        self.lease = SimpleNamespace(
            capabilities={"playwright": "test", "chromium": "test"},
        )

    def run_command(
        self,
        _command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> SimpleNamespace:
        del timeout_seconds
        self.commands.append(_command)
        if "require('playwright')" in _command:
            return SimpleNamespace(
                exit_code=0,
                stdout=(
                    learning_deck_browser_validation.BROWSER_VALIDATION_RESULT_PREFIX
                    + json.dumps(_browser_validation_outcome())
                ),
                stderr="",
            )
        return SimpleNamespace(exit_code=0, stdout="", stderr="")

    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> SimpleNamespace:
        return self.run_command(command, timeout_seconds=timeout_seconds)

    def write_file(self, path: str, text: str) -> None:
        self.files[path] = text

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        del max_bytes
        if path == learning_deck_agent.OUTPUT_INDEX_HTML:
            return _valid_deck_html()
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


class _MissingOutputSandbox(_FakeSandbox):
    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        del max_bytes
        if path == learning_deck_agent.OUTPUT_SOURCE_METADATA:
            return "{}"
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


class _MissingBrowserCapabilitiesSandbox(_FakeSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.lease = SimpleNamespace(
            capabilities={
                "playwright": False,
                "chromium": False,
                "browser_validation_error": "Cannot find module 'playwright'",
            }
        )


class _BrowserValidationFailureSandbox(_FakeSandbox):
    def run_command(
        self,
        _command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> SimpleNamespace:
        del timeout_seconds
        self.commands.append(_command)
        return SimpleNamespace(exit_code=1, stdout="", stderr="ReferenceError: broken deck")


class _RepairingAgent(_FakeAgent):
    calls = 0

    def run_sync(self, *_args: Any, **kwargs: Any) -> _FakeAgentResult:
        type(self).calls += 1
        if type(self).calls == 2:
            sandbox = kwargs["deps"].sandbox
            sandbox.write_file(
                learning_deck_agent.OUTPUT_INDEX_HTML,
                _valid_deck_html("Repaired"),
            )
            sandbox.write_file(
                learning_deck_agent.OUTPUT_SOURCE_NOTES,
                "# Sources\n\n- Primary source.",
            )
        return _FakeAgentResult()


class _Task55Sandbox:
    provider = "local"
    sandbox_id = "task-55-sandbox"
    workspace_posix_root = PurePosixPath("/tmp/newsly/tasks/55")

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.closed = False
        self.commands: list[str] = []
        self.lease = SimpleNamespace(
            capabilities={"playwright": False, "chromium": False},
        )

    def resolve_relative_path(self, path: str) -> str:
        return resolve_workspace_relative_path(
            path,
            workspace_root=self.workspace_posix_root,
        ).as_posix()

    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> SimpleNamespace:
        del timeout_seconds
        self.commands.append(command)
        return SimpleNamespace(exit_code=0, stdout="", stderr="")

    def write_file(self, path: str, text: str) -> None:
        self.files[self.resolve_relative_path(path)] = text

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        resolved_path = self.resolve_relative_path(path)
        try:
            text = self.files[resolved_path]
        except KeyError as exc:
            absolute_path = self.workspace_posix_root / PurePosixPath(resolved_path)
            raise FileNotFoundError(f"path '{absolute_path}' does not exist") from exc
        if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
            raise ValueError("file is too large")
        return text

    def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        return self.read_file(path, max_bytes=max_bytes).encode("utf-8")

    def list_files(self, path: str = ".") -> list[str]:
        resolved_path = self.resolve_relative_path(path)
        prefix = "" if resolved_path == "." else f"{resolved_path.rstrip('/')}/"
        return sorted(candidate for candidate in self.files if candidate.startswith(prefix))

    def close(self) -> None:
        self.closed = True


class _Task55RepairAgent(_FakeAgent):
    calls = 0
    prompts: list[str] = []

    def run_sync(self, prompt: str, *_args: Any, **kwargs: Any) -> _FakeAgentResult:
        type(self).calls += 1
        type(self).prompts.append(prompt)
        sandbox = kwargs["deps"].sandbox
        if type(self).calls == 1:
            sandbox.write_file(
                learning_deck_agent.OUTPUT_INDEX_HTML,
                _valid_deck_html(),
            )
        else:
            sandbox.write_file(
                "/tmp/newsly/tasks/55/output/source-notes.md",
                "# Sources\n\n- Primary source.",
            )
        return _FakeAgentResult()


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
        sandbox_factory=lambda _user_id, _run_id: cast(Any, sandbox),
    )

    assert result.model_provider == "openai"
    assert sandbox.closed is True
    assert sandbox.files["input/interests.txt"] == "Focus on systems"
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


def test_learning_deck_agent_prompt_treats_focus_as_authoritative_instructions() -> None:
    prompt = learning_deck_agent._build_agent_prompt(
        {
            "source_kind": "content",
            "source_title": "Research source",
        },
        "Compare the studies and investigate conflicting results.",
    )

    normalized_prompt = " ".join(prompt.split())
    assert (
        "User instructions: Compare the studies and investigate conflicting results."
        in normalized_prompt
    )
    assert "Treat the user instructions as authoritative additions" in normalized_prompt
    assert "Keep source notes and citations for any additional investigation" in normalized_prompt


def test_learning_deck_agent_uses_generic_vm_session_when_llm_task_exists(
    db_session,
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    llm_task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.LEARNING_DECK,
        mode=LlmTaskMode.LEARNING_DECK_PRESENTATION,
        workflow_key="learning_deck.presentation.v1",
    )
    db_session.commit()
    sandbox = _FakeSandbox()
    calls: list[dict[str, object]] = []

    def fake_create_agent_vm_session(**kwargs):
        calls.append(kwargs)
        return sandbox

    monkeypatch.setattr(learning_deck_agent, "Agent", _FakeAgent)
    monkeypatch.setattr(
        learning_deck_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )
    monkeypatch.setattr(learning_deck_agent, "resolve_model_provider", lambda _model_spec: "openai")
    monkeypatch.setattr(
        learning_deck_agent,
        "create_agent_vm_session",
        fake_create_agent_vm_session,
    )
    monkeypatch.setattr(learning_deck_agent, "monotonic", lambda: 100.0)

    result = learning_deck_agent.run_learning_deck_agent(
        source_snapshot={
            "source_kind": "content",
            "source_identity": "content:88",
            "source_content_id": 88,
            "source_title": "Deck Source",
            "body_text": "Source body for a generated learning deck.",
        },
        interests_prompt=None,
        user_id=test_user.id,
        run_id=456,
        llm_task=llm_task,
    )

    assert result.sandbox_provider == "local"
    assert calls == [
        {
            "user_id": test_user.id,
            "llm_task_id": llm_task.id,
            "vm_namespace": llm_task.vm_namespace,
            "workspace_path": llm_task.workspace_path,
            "shared_workspace_path": llm_task.shared_workspace_path,
            "feature": "learning_deck",
            "deadline": 100.0 + learning_deck_agent.get_settings().llm_task_sandbox_timeout_seconds,
        }
    ]


def test_learning_deck_agent_closes_sandbox_when_agent_deadline_expires(
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    sandbox = _FakeSandbox()
    monkeypatch.setattr(learning_deck_agent, "Agent", _DeadlineAgent)
    monkeypatch.setattr(
        learning_deck_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )
    monkeypatch.setattr(learning_deck_agent, "resolve_model_provider", lambda _model_spec: "openai")

    with pytest.raises(LearningDeckAgentExecutionError, match="agent run expired") as exc_info:
        learning_deck_agent.run_learning_deck_agent(
            source_snapshot={"source_kind": "content", "source_title": "Source"},
            interests_prompt=None,
            user_id=test_user.id,
            run_id=94,
            sandbox_factory=lambda _user_id, _run_id: cast(Any, sandbox),
        )

    assert sandbox.closed is True
    assert any(
        event["event_type"] == "agent_failed"
        and event["payload"]["failure_class"] == "AgentVmDeadlineExceeded"
        for event in exc_info.value.agent_log_events
    )


def test_learning_deck_agent_log_event_accepts_deps_object() -> None:
    events: list[dict[str, Any]] = []
    deps = learning_deck_agent.LearningDeckAgentDeps(
        sandbox=cast(Any, _FakeSandbox()),
        user_id=1,
        run_id=2,
        agent_log_events=events,
    )

    learning_deck_agent._append_agent_log_event(
        deps,
        "read_file",
        {"path": "output/index.html"},
    )

    assert events == [
        {
            "created_at": events[0]["created_at"],
            "event_type": "read_file",
            "payload": {"path": "output/index.html"},
        }
    ]


def test_learning_deck_agent_requires_canonical_llm_task_workspace() -> None:
    with pytest.raises(RuntimeError, match="LLM task workspace is required"):
        learning_deck_agent._create_configured_sandbox(1, llm_task=None)


def test_learning_deck_agent_records_browser_validation_skip_without_blocking_generation(
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    sandbox = _MissingBrowserCapabilitiesSandbox()
    monkeypatch.setattr(learning_deck_agent, "Agent", _FakeAgent)
    monkeypatch.setattr(
        learning_deck_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )
    monkeypatch.setattr(learning_deck_agent, "resolve_model_provider", lambda _model_spec: "openai")

    result = learning_deck_agent.run_learning_deck_agent(
        source_snapshot={"source_kind": "content", "source_title": "Source"},
        interests_prompt=None,
        user_id=test_user.id,
        run_id=93,
        sandbox_factory=lambda _user_id, _run_id: cast(Any, sandbox),
    )

    assert result.browser_validation == {
        "status": "skipped",
        "reason": "sandbox_browser_capabilities_unavailable",
        "missing_capabilities": ["chromium", "playwright"],
        "capability_error": "Cannot find module 'playwright'",
    }
    assert sandbox.closed is True
    assert all("require('playwright')" not in command for command in sandbox.commands)
    assert any(
        event["event_type"] == "browser_validation_skipped"
        and event["payload"] == result.browser_validation
        for event in result.agent_log_events
    )


def test_learning_deck_browser_validation_runs_when_capabilities_are_present() -> None:
    sandbox = _FakeSandbox()

    result = learning_deck_agent._validate_artifact_in_browser(
        cast(Any, sandbox),
        index_html=_valid_deck_html(),
    )

    assert result == _browser_validation_outcome()
    browser_command = next(
        command for command in sandbox.commands if "require('playwright')" in command
    )
    assert "require('playwright')" in browser_command
    assert '"viewport":{"width":390,"height":844}' in browser_command
    assert "window.Reveal.isReady()" in browser_command
    assert "window.Reveal.getCurrentSlide()" in browser_command
    assert "newsly-deck-layout" in browser_command
    assert '"canvas":{"width":720,"height":1280}' in browser_command
    assert '"viewport":{"width":844,"height":390}' in browser_command
    assert '"canvas":{"width":1280,"height":720}' in browser_command
    assert "inspectOrientation" in browser_command
    assert "overflow_slides" in browser_command
    assert "window.Reveal.next()" in browser_command
    assert "window.Reveal.prev()" in browser_command
    assert "single_slide_stable" in browser_command
    assert "single_slide_fragment_round_trip" in browser_command
    assert "page.on('pageerror'" in browser_command
    assert "page.on('requestfailed'" in browser_command
    assert "response.status() >= 400" in browser_command
    viewer_html = sandbox.files[learning_deck_browser_validation.VALIDATION_VIEWER_PATH]
    assert f'id="{DECK_THEME_STYLE_ID}"' in viewer_html
    assert "isResponsiveDeck = true" in viewer_html


def test_browser_validation_tracks_all_deck_resource_failures() -> None:
    sandbox = _FakeSandbox()

    learning_deck_agent._validate_artifact_in_browser(
        cast(Any, sandbox),
        index_html=_valid_deck_html(),
    )

    browser_command = next(
        command for command in sandbox.commands if "require('playwright')" in command
    )
    assert "resourceType === 'script'" not in browser_command
    assert "resourceType === 'stylesheet'" not in browser_command
    assert "['data:', 'blob:', 'about:']" in browser_command
    assert "request.isNavigationRequest()" in browser_command
    assert "request.frame() === page.mainFrame() && url === deckUrl" in browser_command
    assert "return !isSyntheticUrl && !isMainDeckDocument" in browser_command


def test_browser_validation_returns_actionable_skip_for_missing_capabilities() -> None:
    sandbox = _MissingBrowserCapabilitiesSandbox()

    result = learning_deck_agent._validate_artifact_in_browser(
        cast(Any, sandbox),
        index_html=_valid_deck_html(),
    )

    assert result == {
        "status": "skipped",
        "reason": "sandbox_browser_capabilities_unavailable",
        "missing_capabilities": ["chromium", "playwright"],
        "capability_error": "Cannot find module 'playwright'",
    }
    assert sandbox.commands == []


def test_learning_deck_browser_validation_rejects_render_failures_when_available() -> None:
    sandbox = _BrowserValidationFailureSandbox()

    with pytest.raises(
        learning_deck_agent.LearningDeckArtifactError,
        match="Browser validation failed: ReferenceError: broken deck",
    ):
        learning_deck_agent._validate_artifact_in_browser(
            cast(Any, sandbox),
            index_html=_valid_deck_html(),
        )

    assert sum("require('playwright')" in command for command in sandbox.commands) == 1


@pytest.mark.parametrize(
    ("stdout", "expected_error"),
    [
        ("", "did not report a structured outcome"),
        (
            learning_deck_browser_validation.BROWSER_VALIDATION_RESULT_PREFIX + "{broken",
            "reported malformed JSON",
        ),
        (
            learning_deck_browser_validation.BROWSER_VALIDATION_RESULT_PREFIX
            + '{"status":"failed"}',
            "did not report a passing outcome",
        ),
        (
            learning_deck_browser_validation.BROWSER_VALIDATION_RESULT_PREFIX
            + '{"status":"passed"}',
            "reported an incomplete passing outcome",
        ),
    ],
)
def test_browser_validation_rejects_invalid_structured_outcomes(
    stdout: str,
    expected_error: str,
) -> None:
    with pytest.raises(learning_deck_agent.LearningDeckArtifactError, match=expected_error):
        learning_deck_agent._parse_browser_validation_outcome(stdout)


def test_browser_validation_rejects_reported_slide_overflow() -> None:
    outcome = _browser_validation_outcome()
    outcome["portrait"]["overflow_slides"] = ["intro: bottom"]
    stdout = learning_deck_browser_validation.BROWSER_VALIDATION_RESULT_PREFIX + json.dumps(outcome)

    with pytest.raises(
        learning_deck_agent.LearningDeckArtifactError,
        match="reported an incomplete passing outcome",
    ):
        learning_deck_agent._parse_browser_validation_outcome(stdout)


def test_learning_deck_agent_repairs_missing_required_artifacts_once(
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    sandbox = _MissingOutputSandbox()
    _RepairingAgent.calls = 0
    monkeypatch.setattr(learning_deck_agent, "Agent", _RepairingAgent)
    monkeypatch.setattr(
        learning_deck_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )
    monkeypatch.setattr(learning_deck_agent, "resolve_model_provider", lambda _model_spec: "openai")

    result = learning_deck_agent.run_learning_deck_agent(
        source_snapshot={"source_kind": "content", "source_title": "Source"},
        interests_prompt=None,
        user_id=test_user.id,
        run_id=91,
        sandbox_factory=lambda _user_id, _run_id: cast(Any, sandbox),
    )

    assert _RepairingAgent.calls == 2
    assert "Repaired" in result.index_html
    assert any(
        event["event_type"] == "artifact_validation_failed" for event in result.agent_log_events
    )


def test_learning_deck_agent_task_55_absolute_repair_path_converges(
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    sandbox = _Task55Sandbox()
    _Task55RepairAgent.calls = 0
    _Task55RepairAgent.prompts = []
    monkeypatch.setattr(learning_deck_agent, "Agent", _Task55RepairAgent)
    monkeypatch.setattr(
        learning_deck_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )
    monkeypatch.setattr(learning_deck_agent, "resolve_model_provider", lambda _model_spec: "openai")

    result = learning_deck_agent.run_learning_deck_agent(
        source_snapshot={"source_kind": "content", "source_title": "Source"},
        interests_prompt=None,
        user_id=test_user.id,
        run_id=55,
        sandbox_factory=lambda _user_id, _run_id: cast(Any, sandbox),
    )

    assert _Task55RepairAgent.calls == 2
    assert result.source_notes_md == "# Sources\n\n- Primary source."
    assert sandbox.files["output/source-notes.md"] == result.source_notes_md
    assert not any(path.startswith("tmp/newsly/tasks/55") for path in sandbox.files)
    repair_prompt = _Task55RepairAgent.prompts[1]
    assert "All file paths are relative to the workspace root" in repair_prompt
    assert '"missing": ["output/source-notes.md"]' in repair_prompt
    assert 'Current output files: ["output/index.html"]' in repair_prompt
    assert "/tmp/newsly" not in repair_prompt
    validation_event = next(
        event
        for event in result.agent_log_events
        if event["event_type"] == "artifact_validation_failed"
    )
    assert validation_event["payload"]["error"] == (
        "Required output file is missing: output/source-notes.md"
    )
    assert validation_event["payload"]["report"] == {"missing": ["output/source-notes.md"]}
    assert (
        "/tmp/newsly/tasks/55/output/source-notes.md"
        in (validation_event["payload"]["backend_errors"][0]["error"])
    )
    assert sandbox.closed is True


def test_learning_deck_agent_reports_typed_failure_when_repair_does_not_create_outputs(
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    sandbox = _MissingOutputSandbox()
    monkeypatch.setattr(learning_deck_agent, "Agent", _FakeAgent)
    monkeypatch.setattr(
        learning_deck_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )
    monkeypatch.setattr(learning_deck_agent, "resolve_model_provider", lambda _model_spec: "openai")

    with pytest.raises(
        LearningDeckAgentExecutionError,
    ) as exc_info:
        learning_deck_agent.run_learning_deck_agent(
            source_snapshot={"source_kind": "content", "source_title": "Source"},
            interests_prompt=None,
            user_id=test_user.id,
            run_id=92,
            sandbox_factory=lambda _user_id, _run_id: cast(Any, sandbox),
        )

    assert exc_info.value.sandbox_id == sandbox.sandbox_id
    assert exc_info.value.error_type == "artifact_contract_failed"
    assert any(
        event["event_type"] == "artifact_repair_failed" for event in exc_info.value.agent_log_events
    )
