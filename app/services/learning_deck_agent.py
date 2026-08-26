"""Agent loop for building Learning Deck artifacts inside a sandbox."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any, cast

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.settings import ModelSettings

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.db import LlmTask
from app.services.agent_toolset import (
    AGENT_VM_SYSTEM_INSTRUCTIONS,
    AgentToolPolicy,
    AgentToolsetConfig,
    register_agent_vm_tools,
    register_agent_web_search_tool,
)
from app.services.agent_vm_runtime import (
    AgentVmDeadlineExceeded,
    AgentVmSession,
    agent_vm_session_log_payload,
)
from app.services.agent_vm_sessions import create_agent_vm_session
from app.services.learning_deck_artifacts import (
    LearningDeckArtifactError,
    guess_learning_deck_content_type,
    validate_learning_deck_artifact,
)
from app.services.learning_deck_browser_validation import (
    parse_browser_validation_outcome,
    validate_learning_deck_in_browser,
)
from app.services.learning_deck_layout import learning_deck_prompt_values
from app.services.learning_deck_theme import DECK_DESIGN_GUIDE
from app.services.llm_models import build_pydantic_model, resolve_model_provider
from app.services.llm_tasks import require_llm_task_id
from app.services.prompt_library import render_prompt
from app.services.vendor_usage import record_model_usage

logger = get_logger(__name__)

OUTPUT_INDEX_HTML = "output/index.html"
OUTPUT_SOURCE_NOTES = "output/source-notes.md"
OUTPUT_SOURCE_METADATA = "output/source-metadata.json"
OUTPUT_ASSET_DIR = "output/assets"
INPUT_DESIGN_BRIEF = "input/deck-design-brief.md"


@dataclass(frozen=True)
class LearningDeckAgentResult:
    """Generated artifact payload from the learning agent."""

    index_html: str
    source_notes_md: str
    assets: dict[str, tuple[bytes, str]]
    model_provider: str
    model_name: str
    sandbox_provider: str
    sandbox_id: str | None
    source_metadata_updates: dict[str, Any]
    browser_validation: dict[str, Any] = field(default_factory=dict)
    agent_log_events: list[dict[str, Any]] = field(default_factory=list)


class LearningDeckAgentExecutionError(RuntimeError):
    """Raised when the agent fails after a sandbox has started."""

    def __init__(
        self,
        message: str,
        *,
        agent_log_events: list[dict[str, Any]],
        sandbox_provider: str,
        sandbox_id: str | None,
        error_type: str = "agent_execution_failed",
    ) -> None:
        super().__init__(message)
        self.agent_log_events = agent_log_events
        self.sandbox_provider = sandbox_provider
        self.sandbox_id = sandbox_id
        self.error_type = error_type


@dataclass
class LearningDeckAgentDeps:
    """Dependencies exposed to the pydantic-ai tool layer."""

    sandbox: AgentVmSession
    user_id: int
    run_id: int
    agent_log_events: list[dict[str, Any]]


LearningDeckSandboxFactory = Callable[[int, int], AgentVmSession]


LEARNING_DECK_SYSTEM_PROMPT = render_prompt(
    "learning_decks/agent#system",
    **learning_deck_prompt_values(),
)
LEARNING_DECK_DESIGN_BRIEF = (
    render_prompt(
        "learning_decks/agent#design_brief",
        **learning_deck_prompt_values(),
    )
    + "\n\n"
    + DECK_DESIGN_GUIDE
)


def run_learning_deck_agent(
    *,
    source_snapshot: dict[str, Any],
    interests_prompt: str | None,
    user_id: int,
    run_id: int,
    llm_task: LlmTask | None = None,
    sandbox_factory: LearningDeckSandboxFactory | None = None,
) -> LearningDeckAgentResult:
    """Run the coarse agent loop and read the artifact files it produced."""
    settings = get_settings()
    deadline = monotonic() + settings.llm_task_sandbox_timeout_seconds
    sandbox = (
        sandbox_factory(user_id, run_id)
        if sandbox_factory is not None
        else _create_configured_sandbox(
            user_id,
            llm_task=llm_task,
            deadline=deadline,
        )
    )
    agent_log_events: list[dict[str, Any]] = []
    try:
        _append_agent_log_event(
            agent_log_events,
            "sandbox_started",
            agent_vm_session_log_payload(sandbox),
        )
        _prepare_sandbox_inputs(
            sandbox,
            source_snapshot=source_snapshot,
            interests_prompt=interests_prompt,
        )
        model_spec = settings.learning_deck_model
        provider = resolve_model_provider(model_spec)
        model, base_model_settings = build_pydantic_model(model_spec)
        agent: Agent[LearningDeckAgentDeps, str] = Agent(
            model,
            deps_type=LearningDeckAgentDeps,
            output_type=str,
            system_prompt=(f"{LEARNING_DECK_SYSTEM_PROMPT}\n\n{AGENT_VM_SYSTEM_INSTRUCTIONS}"),
        )
        _register_tools(agent, llm_task=llm_task)

        try:
            result = agent.run_sync(
                _build_agent_prompt(source_snapshot, interests_prompt),
                deps=LearningDeckAgentDeps(
                    sandbox=sandbox,
                    user_id=user_id,
                    run_id=run_id,
                    agent_log_events=agent_log_events,
                ),
                model_settings=_build_runtime_model_settings(
                    base_model_settings,
                    deadline=deadline,
                ),
                usage_limits=UsageLimits(request_limit=None),
            )
        except Exception as exc:
            _append_agent_log_event(
                agent_log_events,
                "agent_failed",
                {"error": str(exc), "failure_class": type(exc).__name__},
            )
            raise LearningDeckAgentExecutionError(
                str(exc),
                agent_log_events=agent_log_events,
                sandbox_provider=sandbox.provider,
                sandbox_id=sandbox.sandbox_id,
            ) from exc
        _append_agent_log_event(
            agent_log_events,
            "agent_completed",
            {"output_chars": len(str(result.output or ""))},
        )
        record_model_usage(
            "learning_deck_generate",
            result,
            model_spec=model_spec,
            persist={
                "provider": provider,
                "feature": "learning_deck_generation",
                "operation": "learning_deck.generate",
                "source": "queue",
                "content_id": _learning_deck_usage_content_id(source_snapshot),
                "user_id": user_id,
                "metadata": _learning_deck_usage_metadata(
                    source_snapshot,
                    run_id=run_id,
                ),
            },
        )
        logger.info(
            "Learning Deck agent completed",
            extra={
                "component": "learning_decks",
                "operation": "agent_run",
                "status": "completed",
                "item_id": run_id,
                "user_id": user_id,
                "context_data": {
                    "model": model_spec,
                    "sandbox_provider": sandbox.provider,
                    "sandbox_id": sandbox.sandbox_id,
                    "agent_output_chars": len(str(result.output or "")),
                },
            },
        )

        try:
            index_html, source_notes_md, browser_validation = _read_and_validate_required_artifacts(
                sandbox
            )
            _append_browser_validation_event(agent_log_events, browser_validation)
        except Exception as first_error:  # noqa: BLE001
            _append_agent_log_event(
                agent_log_events,
                "artifact_validation_failed",
                _artifact_error_log_payload(first_error),
            )
            try:
                repair_result = agent.run_sync(
                    _artifact_repair_prompt(first_error, sandbox),
                    deps=LearningDeckAgentDeps(
                        sandbox=sandbox,
                        user_id=user_id,
                        run_id=run_id,
                        agent_log_events=agent_log_events,
                    ),
                    model_settings=_build_runtime_model_settings(
                        base_model_settings,
                        deadline=deadline,
                    ),
                    usage_limits=UsageLimits(request_limit=None),
                )
                record_model_usage(
                    "learning_deck_repair",
                    repair_result,
                    model_spec=model_spec,
                    persist={
                        "provider": provider,
                        "feature": "learning_deck_generation",
                        "operation": "learning_deck.repair",
                        "source": "queue",
                        "content_id": _learning_deck_usage_content_id(source_snapshot),
                        "user_id": user_id,
                        "metadata": _learning_deck_usage_metadata(source_snapshot, run_id=run_id),
                    },
                )
                index_html, source_notes_md, browser_validation = (
                    _read_and_validate_required_artifacts(sandbox)
                )
                _append_browser_validation_event(agent_log_events, browser_validation)
            except Exception as repair_error:  # noqa: BLE001
                _append_agent_log_event(
                    agent_log_events,
                    "artifact_repair_failed",
                    _artifact_error_log_payload(repair_error),
                )
                raise LearningDeckAgentExecutionError(
                    str(repair_error),
                    agent_log_events=agent_log_events,
                    sandbox_provider=sandbox.provider,
                    sandbox_id=sandbox.sandbox_id,
                    error_type="artifact_contract_failed",
                ) from repair_error

        return LearningDeckAgentResult(
            index_html=index_html,
            source_notes_md=source_notes_md,
            assets=_collect_assets(sandbox),
            model_provider=provider,
            model_name=model_spec,
            sandbox_provider=sandbox.provider,
            sandbox_id=sandbox.sandbox_id,
            source_metadata_updates=_read_source_metadata_updates(sandbox),
            browser_validation=browser_validation,
            agent_log_events=agent_log_events,
        )
    finally:
        sandbox.close()


def _register_tools(agent: Agent[LearningDeckAgentDeps, str], *, llm_task: LlmTask | None) -> None:
    policy = AgentToolPolicy.from_mapping(
        llm_task.tool_policy if llm_task is not None else None,
    )
    config = AgentToolsetConfig(
        feature="learning_deck_generation",
        operation_prefix="learning_deck",
        source="queue",
        tool_policy=policy,
    )
    register_agent_vm_tools(
        agent,
        session_getter=lambda deps: deps.sandbox,
        log_event=_append_agent_log_event,
        config=config,
    )
    if policy.web_search:
        register_agent_web_search_tool(
            agent,
            log_event=_append_agent_log_event,
            user_id_getter=lambda deps: deps.user_id,
            metadata_getter=lambda deps: {"run_id": deps.run_id},
            config=config,
        )


def _read_and_validate_required_artifacts(
    sandbox: AgentVmSession,
) -> tuple[str, str, dict[str, Any]]:
    settings = get_settings()
    required_files = (
        (OUTPUT_INDEX_HTML, settings.learning_deck_max_index_html_bytes),
        (OUTPUT_SOURCE_NOTES, settings.learning_deck_max_source_notes_bytes),
    )
    artifacts: dict[str, str] = {}
    missing: list[str] = []
    backend_errors: list[dict[str, str]] = []
    for path, max_bytes in required_files:
        try:
            artifacts[path] = sandbox.read_file(path, max_bytes=max_bytes)
        except Exception as exc:  # noqa: BLE001
            missing.append(path)
            backend_errors.append(
                {
                    "path": path,
                    "error": str(exc),
                    "failure_class": type(exc).__name__,
                }
            )
    if missing:
        raise LearningDeckArtifactError(
            f"Required output file is missing: {', '.join(missing)}",
            report={"missing": missing},
            backend_errors=backend_errors,
        )

    index_html = artifacts[OUTPUT_INDEX_HTML]
    source_notes_md = artifacts[OUTPUT_SOURCE_NOTES]
    validate_learning_deck_artifact(
        index_html=index_html,
        source_notes_md=source_notes_md,
    )
    browser_validation = _validate_artifact_in_browser(sandbox, index_html=index_html)
    return index_html, source_notes_md, browser_validation


def _validate_artifact_in_browser(
    sandbox: AgentVmSession,
    *,
    index_html: str,
) -> dict[str, Any]:
    return validate_learning_deck_in_browser(sandbox, index_html=index_html)


def _parse_browser_validation_outcome(stdout: str) -> dict[str, Any]:
    return parse_browser_validation_outcome(stdout)


def _append_browser_validation_event(
    agent_log_events: list[dict[str, Any]],
    browser_validation: dict[str, Any],
) -> None:
    status = browser_validation.get("status")
    event_type = "browser_validation_passed" if status == "passed" else "browser_validation_skipped"
    _append_agent_log_event(agent_log_events, event_type, browser_validation)


def _artifact_repair_prompt(error: Exception, sandbox: AgentVmSession) -> str:
    if isinstance(error, LearningDeckArtifactError):
        report = error.report or {"validation_error": str(error)}
    else:
        report = {"validation_error": "Generated artifact validation failed unexpectedly"}
    try:
        output_files = sandbox.list_files("output")[:50]
    except Exception:  # noqa: BLE001
        output_files = []
    return (
        "The generated artifact did not satisfy the output contract. Fix only the output files "
        "in the existing workspace, then verify them. You must create output/index.html and "
        "output/source-notes.md. All file paths are relative to the workspace root; never use "
        "absolute paths. Do not restart the research. "
        f"Validation report: {json.dumps(report, sort_keys=True)}. "
        f"Current output files: {json.dumps(output_files)}."
    )


def _artifact_error_log_payload(error: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": str(error),
        "failure_class": type(error).__name__,
    }
    if isinstance(error, LearningDeckArtifactError):
        if error.report:
            payload["report"] = error.report
        if error.backend_errors:
            payload["backend_errors"] = error.backend_errors
    return payload


def _create_configured_sandbox(
    user_id: int,
    *,
    llm_task: LlmTask | None = None,
    deadline: float | None = None,
) -> AgentVmSession:
    if llm_task is None or not llm_task.workspace_path or not llm_task.shared_workspace_path:
        raise RuntimeError("Learning Deck LLM task workspace is required")
    return create_agent_vm_session(
        user_id=user_id,
        llm_task_id=require_llm_task_id(llm_task),
        vm_namespace=llm_task.vm_namespace or f"user:{user_id}",
        workspace_path=llm_task.workspace_path,
        shared_workspace_path=llm_task.shared_workspace_path,
        feature="learning_deck",
        deadline=deadline,
    )


def _prepare_sandbox_inputs(
    sandbox: AgentVmSession,
    *,
    source_snapshot: dict[str, Any],
    interests_prompt: str | None,
) -> None:
    sandbox.execute_bash("mkdir -p input output/assets")
    body_text = source_snapshot.get("body_text")
    snapshot_for_json = {key: value for key, value in source_snapshot.items() if key != "body_text"}
    if isinstance(body_text, str) and body_text.strip():
        snapshot_for_json["body_text_file"] = "input/source.txt"
        snapshot_for_json["body_text_chars"] = len(body_text)
        sandbox.write_file("input/source.txt", body_text)
    else:
        sandbox.write_file(
            "input/source.txt",
            "No primary source text was provided. Use input/source-snapshot.json for source "
            "metadata and inspect the source URL with bash or web search when needed.",
        )
    sandbox.write_file(
        "input/source-snapshot.json",
        json.dumps(snapshot_for_json, indent=2, sort_keys=True, default=str),
    )
    sandbox.write_file("input/interests.txt", interests_prompt or "")
    sandbox.write_file(INPUT_DESIGN_BRIEF, LEARNING_DECK_DESIGN_BRIEF)


def _build_agent_prompt(source_snapshot: dict[str, Any], interests_prompt: str | None) -> str:
    source_kind = source_snapshot.get("source_kind")
    source_title = source_snapshot.get("source_title") or source_snapshot.get("source_url")
    interests = (
        interests_prompt.strip() if interests_prompt else "No additional instructions given."
    )
    return render_prompt(
        "learning_decks/agent#user",
        **learning_deck_prompt_values(),
        source_title=source_title,
        source_kind=source_kind,
        interests=interests,
        github_guidance=_build_github_guidance(source_snapshot),
    )


def _build_github_guidance(source_snapshot: dict[str, Any]) -> str:
    if source_snapshot.get("source_kind") != "github_repo":
        return ""

    source_metadata = source_snapshot.get("source_metadata")
    metadata = source_metadata if isinstance(source_metadata, dict) else {}
    linked_artifact = metadata.get("linked_artifact")
    artifact = linked_artifact if isinstance(linked_artifact, dict) else {}
    artifact_path = artifact.get("path")
    artifact_ref = artifact.get("ref")
    artifact_raw_url = artifact.get("raw_url")
    artifact_blob_url = artifact.get("url")

    guidance = (
        "\nFor this GitHub source, treat the request as research over the repository, not as "
        "normal URL ingestion. Clone or inspect the public repo, resolve the default branch and "
        "current commit SHA, inspect the README/docs/source tree with bash/code tools, and write "
        "the inspected files, branch, commit, and rationale in source notes and "
        "output/source-metadata.json."
    )
    if artifact_path or artifact_raw_url or artifact_blob_url:
        guidance += (
            " The shared URL points at a specific GitHub file/blob; inspect the repository and "
            "also download/read the raw linked artifact. Do not treat the GitHub HTML blob page "
            "as the artifact contents."
        )
    if artifact_path:
        guidance += f" Linked artifact path: {artifact_path}."
    if artifact_ref:
        guidance += f" Linked artifact ref: {artifact_ref}."
    if artifact_raw_url:
        guidance += f" Raw artifact URL: {artifact_raw_url}."
    return guidance


def _build_runtime_model_settings(
    base_model_settings: ModelSettings | None,
    *,
    deadline: float,
) -> ModelSettings:
    runtime_settings = dict(base_model_settings or {})
    runtime_settings["timeout"] = _remaining_run_timeout(deadline)
    return cast(ModelSettings, runtime_settings)


def _remaining_run_timeout(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise AgentVmDeadlineExceeded("Learning Deck agent deadline was exceeded")
    return remaining


def _learning_deck_usage_content_id(source_snapshot: dict[str, Any]) -> int | None:
    content_id = source_snapshot.get("source_content_id")
    return content_id if isinstance(content_id, int) else None


def _learning_deck_usage_metadata(
    source_snapshot: dict[str, Any],
    *,
    run_id: int,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"run_id": run_id}
    for key in ("source_kind", "source_identity"):
        value = source_snapshot.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value
    content_id = _learning_deck_usage_content_id(source_snapshot)
    if content_id is not None:
        metadata["source_content_id"] = content_id
    return metadata


def _collect_assets(sandbox: AgentVmSession) -> dict[str, tuple[bytes, str]]:
    settings = get_settings()
    files = sandbox.list_files(OUTPUT_ASSET_DIR)
    assets: dict[str, tuple[bytes, str]] = {}
    for path in files[: settings.learning_deck_max_asset_count]:
        normalized = path.strip().lstrip("./")
        if not normalized.startswith(f"{OUTPUT_ASSET_DIR}/"):
            continue
        relative_path = normalized.removeprefix("output/")
        assets[relative_path] = (
            sandbox.read_file_bytes(normalized, max_bytes=settings.learning_deck_max_asset_bytes),
            guess_learning_deck_content_type(relative_path),
        )
    return assets


def _read_source_metadata_updates(sandbox: AgentVmSession) -> dict[str, Any]:
    try:
        raw_json = sandbox.read_file(OUTPUT_SOURCE_METADATA, max_bytes=100_000)
    except Exception:
        return {}
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _append_agent_log_event(
    deps_or_events: LearningDeckAgentDeps | list[dict[str, Any]],
    event_type: str,
    payload: dict[str, Any],
) -> None:
    events = (
        deps_or_events.agent_log_events
        if isinstance(deps_or_events, LearningDeckAgentDeps)
        else deps_or_events
    )
    events.append(
        {
            "created_at": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
    )
