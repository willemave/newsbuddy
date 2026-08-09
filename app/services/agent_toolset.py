"""Shared pydantic-ai tool registration for VM-backed agents."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent, RunContext

from app.services.agent_vm_runtime import AgentVmSession
from app.services.exa_client import exa_search


@dataclass(frozen=True)
class AgentToolPolicy:
    """Concrete tool availability for one VM-backed agent run."""

    execute_bash: bool = True
    write_file: bool = True
    read_file: bool = True
    list_files: bool = True
    web_search: bool = True

    @classmethod
    def from_mapping(cls, policy: Mapping[str, Any] | None) -> AgentToolPolicy:
        if not policy:
            return cls()

        files_policy = policy.get("files")
        file_tools_enabled = _policy_flag(files_policy, default=True)
        read_tools_enabled = file_tools_enabled
        write_tools_enabled = file_tools_enabled
        if isinstance(files_policy, str):
            normalized_files_policy = files_policy.strip().lower()
            if normalized_files_policy in {"read", "read_only", "readonly"}:
                write_tools_enabled = False
                read_tools_enabled = True
            elif normalized_files_policy in {"none", "disabled", "off"}:
                write_tools_enabled = False
                read_tools_enabled = False

        return cls(
            execute_bash=_policy_flag(policy.get("execute_bash"), default=True),
            write_file=_policy_flag(
                policy.get("write_file"),
                default=write_tools_enabled,
            ),
            read_file=_policy_flag(policy.get("read_file"), default=read_tools_enabled),
            list_files=_policy_flag(policy.get("list_files"), default=read_tools_enabled),
            web_search=_policy_flag(policy.get("web_search"), default=True),
        )


@dataclass(frozen=True)
class AgentToolsetConfig:
    """Configuration for a shared VM toolset registration."""

    feature: str
    operation_prefix: str
    source: str
    max_read_bytes: int = 100_000
    max_search_results: int = 8
    default_bash_timeout_seconds: int = 120
    max_bash_timeout_seconds: int = 300
    tool_policy: AgentToolPolicy = field(default_factory=AgentToolPolicy)


SessionGetter = Callable[[Any], AgentVmSession]
LogEventCallback = Callable[[Any, str, dict[str, Any]], None]
UserIdGetter = Callable[[Any], int | None]
MetadataGetter = Callable[[Any], dict[str, Any]]


def register_agent_vm_tools(
    agent: Agent[Any, Any],
    *,
    session_getter: SessionGetter,
    log_event: LogEventCallback,
    user_id_getter: UserIdGetter,
    metadata_getter: MetadataGetter,
    config: AgentToolsetConfig,
) -> None:
    """Register the stable VM tool surface on a pydantic-ai agent."""
    tool_policy = config.tool_policy

    def _execute_bash_impl(
        ctx: RunContext[Any],
        command: str,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        session = session_getter(ctx.deps)
        bounded_timeout_seconds = _bounded_bash_timeout(timeout_seconds, config=config)
        result = session.execute_bash(command, timeout_seconds=bounded_timeout_seconds)
        log_event(
            ctx.deps,
            "execute_bash",
            {
                "command": command,
                "requested_timeout_seconds": timeout_seconds,
                "timeout_seconds": bounded_timeout_seconds,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
        return {
            "ok": result.exit_code == 0,
            "exit_code": result.exit_code,
            "stdout": _bounded_text(result.stdout),
            "stderr": _bounded_text(result.stderr),
        }

    if tool_policy.execute_bash:

        @agent.tool
        def execute_bash(
            ctx: RunContext[Any],
            command: str,
            timeout_seconds: int | None = None,
        ) -> dict[str, Any]:
            """Run a bash command inside the VM workspace."""
            return _execute_bash_impl(ctx, command, timeout_seconds=timeout_seconds)

    if tool_policy.write_file:

        @agent.tool
        def write_file(ctx: RunContext[Any], path: str, text: str) -> dict[str, Any]:
            """Write a UTF-8 file below the VM workspace."""
            session_getter(ctx.deps).write_file(path, text)
            log_event(ctx.deps, "write_file", {"path": path, "chars": len(text)})
            return {"ok": True, "path": path, "chars": len(text)}

    if tool_policy.read_file:

        @agent.tool
        def read_file(
            ctx: RunContext[Any],
            path: str,
            max_bytes: int | None = None,
        ) -> dict[str, Any]:
            """Read a UTF-8 file below the VM workspace."""
            bounded_max_bytes = _bounded_read_limit(max_bytes, config.max_read_bytes)
            try:
                text = session_getter(ctx.deps).read_file(path, max_bytes=bounded_max_bytes)
            except Exception as exc:  # noqa: BLE001
                message = f"File not found or unreadable: {path}"
                log_event(
                    ctx.deps,
                    "read_file_failed",
                    {"path": path, "error": str(exc), "failure_class": type(exc).__name__},
                )
                return {"ok": False, "path": path, "error": message}
            log_event(ctx.deps, "read_file", {"path": path, "chars": len(text)})
            return {"ok": True, "path": path, "text": text}

    if tool_policy.list_files:

        @agent.tool
        def list_files(ctx: RunContext[Any], path: str = ".") -> dict[str, Any]:
            """List files below a VM workspace path."""
            files = session_getter(ctx.deps).list_files(path)
            log_event(ctx.deps, "list_files", {"path": path, "files": files})
            return {"ok": True, "path": path, "files": files}

    if tool_policy.web_search:

        @agent.tool
        def web_search(
            ctx: RunContext[Any],
            query: str,
            num_results: int = 5,
            category: str | None = None,
        ) -> dict[str, Any]:
            """Search the web with Newsly's configured Exa client."""
            del category
            bounded_results = min(max(int(num_results), 1), config.max_search_results)
            results = exa_search(
                query,
                num_results=bounded_results,
                max_characters=2500,
                telemetry={
                    "feature": config.feature,
                    "operation": f"{config.operation_prefix}.web_search",
                    "source": config.source,
                    "user_id": user_id_getter(ctx.deps),
                    "metadata": metadata_getter(ctx.deps),
                },
            )
            log_event(
                ctx.deps,
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
            return {
                "ok": True,
                "query": query,
                "results": [
                    {
                        "title": result.title,
                        "url": result.url,
                        "published_date": result.published_date,
                        "snippet": result.snippet,
                    }
                    for result in results
                ],
            }


def _bounded_text(value: str, *, max_chars: int = 20_000) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n...[truncated]"


def _bounded_read_limit(requested: int | None, default_max: int) -> int:
    if requested is None:
        return default_max
    return min(max(int(requested), 1), default_max)


def _bounded_bash_timeout(
    requested: int | None,
    *,
    config: AgentToolsetConfig,
) -> int:
    maximum = max(1, int(config.max_bash_timeout_seconds))
    default = min(max(1, int(config.default_bash_timeout_seconds)), maximum)
    if requested is None:
        return default
    return min(max(1, int(requested)), maximum)


def _policy_flag(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "disabled", "none"}
    return bool(value)
