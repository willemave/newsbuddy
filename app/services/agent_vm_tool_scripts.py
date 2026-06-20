"""Workspace helper scripts installed into generic agent VMs."""

from __future__ import annotations

import shlex

from app.core.settings import get_settings
from app.models.db import LlmTask
from app.services.agent_toolset import AgentToolPolicy
from app.services.agent_vm_runtime import AgentVmSession
from app.services.llm_task_tools import LLM_TASK_WEB_SEARCH_TOOL, create_llm_task_tool_token
from app.services.llm_tasks import require_llm_task_id

NEWSLY_WEB_SEARCH_SCRIPT_PATH = ".newsly/bin/newsly-web-search"
NEWSLY_AGENT_ENV_PATH = ".newsly/env"

NEWSLY_WEB_SEARCH_SCRIPT = r'''#!/usr/bin/env python3
"""Task-scoped Newsly web search helper for VM agent workflows."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="newsly-web-search",
        description="Search the web through Newsly's host-mediated Exa service.",
    )
    parser.add_argument("query", nargs="?", help="Search query.")
    parser.add_argument("--query", dest="query_option", help="Search query.")
    parser.add_argument("--limit", "--num-results", type=int, default=5, dest="limit")
    parser.add_argument("--category", help="Optional Exa category.")
    parser.add_argument("--format", choices=("json", "urls"), default="json")
    parser.add_argument("--out", help="Write output to this file instead of stdout.")
    args = parser.parse_args()

    query = args.query_option or args.query
    if not query:
        parser.error("a query is required")

    base_url = os.environ.get("NEWSLY_AGENT_API_BASE", "").rstrip("/")
    token = os.environ.get("NEWSLY_AGENT_TASK_TOKEN")
    task_id = os.environ.get("NEWSLY_LLM_TASK_ID")
    missing = [
        name
        for name, value in (
            ("NEWSLY_AGENT_API_BASE", base_url),
            ("NEWSLY_AGENT_TASK_TOKEN", token),
            ("NEWSLY_LLM_TASK_ID", task_id),
        )
        if not value
    ]
    if missing:
        print(f"Missing required environment: {', '.join(missing)}", file=sys.stderr)
        return 2

    request_payload = {
        "query": query,
        "num_results": max(1, min(int(args.limit), 10)),
        "category": args.category,
    }
    request = urllib.request.Request(
        f"{base_url}/api/llm-tasks/{task_id}/tools/web-search",
        data=json.dumps(request_payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "newsly-web-search/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"Search request failed with HTTP {exc.code}: {body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Search request failed: {exc}", file=sys.stderr)
        return 1

    if args.format == "urls":
        output = "\n".join(result.get("url", "") for result in payload.get("results", []))
        output = output.strip() + ("\n" if output.strip() else "")
    else:
        output = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    if args.out:
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def install_agent_vm_tool_scripts(
    session: AgentVmSession,
    *,
    llm_task_id: int,
    api_base_url: str,
    task_token: str,
) -> None:
    """Install Newsly helper commands and task-scoped environment into a VM workspace."""
    session.execute_bash("mkdir -p .newsly/bin")
    session.write_file(NEWSLY_WEB_SEARCH_SCRIPT_PATH, NEWSLY_WEB_SEARCH_SCRIPT)
    session.write_file(
        NEWSLY_AGENT_ENV_PATH,
        "\n".join(
            [
                f"NEWSLY_AGENT_API_BASE={shlex.quote(api_base_url.rstrip('/'))}",
                f"NEWSLY_AGENT_TASK_TOKEN={shlex.quote(task_token)}",
                f"NEWSLY_LLM_TASK_ID={shlex.quote(str(llm_task_id))}",
                "",
            ]
        ),
    )
    session.execute_bash(f"chmod +x {shlex.quote(NEWSLY_WEB_SEARCH_SCRIPT_PATH)}")


def install_agent_vm_task_tools(session: AgentVmSession, *, task: LlmTask) -> None:
    """Install helper commands allowed by one persisted LLM task policy."""
    if not AgentToolPolicy.from_mapping(task.tool_policy).web_search:
        return
    task_token = create_llm_task_tool_token(task, tool_name=LLM_TASK_WEB_SEARCH_TOOL)
    install_agent_vm_tool_scripts(
        session,
        llm_task_id=require_llm_task_id(task),
        api_base_url=get_settings().llm_task_agent_api_base_url,
        task_token=task_token,
    )
