"""Agent loop for building Learning Deck artifacts inside a sandbox."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from pydantic_ai import Agent, RunContext
from pydantic_ai.settings import ModelSettings

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.exa_client import ExaSearchResult, exa_search
from app.services.learning_deck_sandbox import (
    LearningDeckSandboxSession,
    create_learning_deck_sandbox_session,
    guess_asset_content_type,
)
from app.services.learning_deck_theme import DECK_DESIGN_GUIDE
from app.services.llm_models import build_pydantic_model, resolve_model_provider
from app.services.prompt_library import load_prompt, render_prompt
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
    ) -> None:
        super().__init__(message)
        self.agent_log_events = agent_log_events
        self.sandbox_provider = sandbox_provider
        self.sandbox_id = sandbox_id


@dataclass
class LearningDeckAgentDeps:
    """Dependencies exposed to the pydantic-ai tool layer."""

    sandbox: LearningDeckSandboxSession
    user_id: int
    run_id: int
    agent_log_events: list[dict[str, Any]]


LearningDeckSandboxFactory = Callable[[int, int], LearningDeckSandboxSession]


LEARNING_DECK_SYSTEM_PROMPT = load_prompt("learning_decks/agent#system")
LEARNING_DECK_DESIGN_BRIEF = (
    load_prompt("learning_decks/agent#design_brief") + "\n\n" + DECK_DESIGN_GUIDE
)


def run_learning_deck_agent(
    *,
    source_snapshot: dict[str, Any],
    interests_prompt: str | None,
    user_id: int,
    run_id: int,
    sandbox_factory: LearningDeckSandboxFactory | None = None,
) -> LearningDeckAgentResult:
    """Run the coarse agent loop and read the artifact files it produced."""
    settings = get_settings()
    sandbox_factory = sandbox_factory or _create_configured_sandbox
    sandbox = sandbox_factory(user_id, run_id)
    agent_log_events: list[dict[str, Any]] = []
    try:
        _append_agent_log_event(
            agent_log_events,
            "sandbox_started",
            {"provider": sandbox.provider, "sandbox_id": sandbox.sandbox_id},
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
            system_prompt=LEARNING_DECK_SYSTEM_PROMPT,
        )
        _register_tools(agent)

        try:
            result = agent.run_sync(
                _build_agent_prompt(source_snapshot, interests_prompt),
                deps=LearningDeckAgentDeps(
                    sandbox=sandbox,
                    user_id=user_id,
                    run_id=run_id,
                    agent_log_events=agent_log_events,
                ),
                model_settings=_build_runtime_model_settings(base_model_settings),
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

        return LearningDeckAgentResult(
            index_html=sandbox.read_file(
                OUTPUT_INDEX_HTML,
                max_bytes=settings.learning_deck_max_index_html_bytes,
            ),
            source_notes_md=sandbox.read_file(
                OUTPUT_SOURCE_NOTES,
                max_bytes=settings.learning_deck_max_source_notes_bytes,
            ),
            assets=_collect_assets(sandbox),
            model_provider=provider,
            model_name=model_spec,
            sandbox_provider=sandbox.provider,
            sandbox_id=sandbox.sandbox_id,
            source_metadata_updates=_read_source_metadata_updates(sandbox),
            agent_log_events=agent_log_events,
        )
    finally:
        sandbox.close()


def _register_tools(agent: Agent[LearningDeckAgentDeps, str]) -> None:
    @agent.tool
    def bash(
        ctx: RunContext[LearningDeckAgentDeps],
        command: str,
        timeout_seconds: int | None = None,
    ) -> str:
        """Run a bash command inside the sandbox."""
        result = ctx.deps.sandbox.run_command(command, timeout_seconds=timeout_seconds)
        _append_agent_log_event(
            ctx.deps.agent_log_events,
            "bash",
            {
                "command": command,
                "timeout_seconds": timeout_seconds,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
        return f"exit_code={result.exit_code}\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"

    @agent.tool
    def write_file(ctx: RunContext[LearningDeckAgentDeps], path: str, text: str) -> str:
        """Write a UTF-8 file below the sandbox workdir."""
        ctx.deps.sandbox.write_file(path, text)
        _append_agent_log_event(
            ctx.deps.agent_log_events,
            "write_file",
            {"path": path, "chars": len(text)},
        )
        return f"Wrote {path}"

    @agent.tool
    def read_file(ctx: RunContext[LearningDeckAgentDeps], path: str) -> str:
        """Read a UTF-8 file below the sandbox workdir."""
        try:
            text = ctx.deps.sandbox.read_file(path, max_bytes=100_000)
        except Exception as exc:
            message = f"File not found or unreadable: {path}"
            _append_agent_log_event(
                ctx.deps.agent_log_events,
                "read_file_failed",
                {"path": path, "error": str(exc), "failure_class": type(exc).__name__},
            )
            return message
        _append_agent_log_event(
            ctx.deps.agent_log_events,
            "read_file",
            {"path": path, "chars": len(text)},
        )
        return text

    @agent.tool
    def list_files(ctx: RunContext[LearningDeckAgentDeps], path: str = ".") -> str:
        """List files below a sandbox workdir path."""
        files = ctx.deps.sandbox.list_files(path)
        _append_agent_log_event(
            ctx.deps.agent_log_events,
            "list_files",
            {"path": path, "files": files},
        )
        return "\n".join(files) if files else "No files found."

    @agent.tool
    def web_search(
        ctx: RunContext[LearningDeckAgentDeps],
        query: str,
        num_results: int = 5,
    ) -> str:
        """Search the web with Newsly's configured Exa client."""
        bounded_results = min(max(int(num_results), 1), 8)
        results = exa_search(
            query,
            num_results=bounded_results,
            max_characters=2500,
            telemetry={
                "feature": "learning_deck_generation",
                "operation": "learning_deck.web_search",
                "source": "queue",
                "user_id": ctx.deps.user_id,
                "metadata": {"run_id": ctx.deps.run_id},
            },
        )
        _append_agent_log_event(
            ctx.deps.agent_log_events,
            "web_search",
            {
                "query": query,
                "num_results": bounded_results,
                "results": [
                    {
                        "title": result.title,
                        "url": result.url,
                        "published_date": result.published_date,
                    }
                    for result in results
                ],
            },
        )
        return _format_search_results(results)


def _create_configured_sandbox(user_id: int, run_id: int) -> LearningDeckSandboxSession:
    return create_learning_deck_sandbox_session(user_id=user_id, run_id=run_id)


def _prepare_sandbox_inputs(
    sandbox: LearningDeckSandboxSession,
    *,
    source_snapshot: dict[str, Any],
    interests_prompt: str | None,
) -> None:
    sandbox.run_command("mkdir -p input output/assets")
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
    interests = interests_prompt.strip() if interests_prompt else "No additional interests given."
    github_guidance = ""
    if source_kind == "github_repo":
        github_guidance = (
            "\nFor this GitHub repository, clone or inspect the public repo, resolve the default "
            "branch and current commit SHA, inspect the architecture with bash/code tools, and "
            "write those details in source notes and output/source-metadata.json."
        )
    return render_prompt(
        "learning_decks/agent#user",
        source_title=source_title,
        source_kind=source_kind,
        interests=interests,
        github_guidance=github_guidance,
    )


def _build_runtime_model_settings(base_model_settings: ModelSettings | None) -> ModelSettings:
    settings = get_settings()
    runtime_settings = dict(base_model_settings or {})
    runtime_settings["timeout"] = settings.learning_sandbox_timeout_seconds
    return cast(ModelSettings, runtime_settings)


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


def _collect_assets(sandbox: LearningDeckSandboxSession) -> dict[str, tuple[bytes, str]]:
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
            guess_asset_content_type(relative_path),
        )
    return assets


def _read_source_metadata_updates(sandbox: LearningDeckSandboxSession) -> dict[str, Any]:
    try:
        raw_json = sandbox.read_file(OUTPUT_SOURCE_METADATA, max_bytes=100_000)
    except Exception:
        return {}
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _format_search_results(results: list[ExaSearchResult]) -> str:
    if not results:
        return "No web search results available."
    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        lines.append(f"[{index}] {result.title}")
        lines.append(f"URL: {result.url}")
        if result.published_date:
            lines.append(f"Published: {result.published_date}")
        if result.snippet:
            lines.append(f"Snippet: {result.snippet}")
        lines.append("")
    return "\n".join(lines).strip()


def _append_agent_log_event(
    events: list[dict[str, Any]],
    event_type: str,
    payload: dict[str, Any],
) -> None:
    events.append(
        {
            "created_at": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
    )
