"""Entry point for the remote Newsly agent CLI."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from cli.newsly_agent.client import AgentClientError, NewslyAgentClient, WaitOptions
from cli.newsly_agent.commands import content, digests, jobs, onboarding, search, sources
from cli.newsly_agent.config import (
    AgentCliConfig,
    get_config_path,
    load_config,
    update_config,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(prog="newsly-agent")
    parser.add_argument("--config", help="Override the CLI config path")
    parser.add_argument("--output", choices=("json", "text"), default="json")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")

    subparsers = parser.add_subparsers(dest="command_group", required=True)

    config_parser = subparsers.add_parser("config")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    set_server = config_subparsers.add_parser("set-server")
    set_server.add_argument("server_url")
    set_server.set_defaults(handler=handle_set_server)
    set_api_key = config_subparsers.add_parser("set-api-key")
    set_api_key.add_argument("api_key")
    set_api_key.set_defaults(handler=handle_set_api_key)

    jobs_parser = subparsers.add_parser("jobs")
    jobs_subparsers = jobs_parser.add_subparsers(dest="jobs_command", required=True)
    jobs_get = jobs_subparsers.add_parser("get")
    jobs_get.add_argument("job_id", type=int)
    jobs_get.set_defaults(handler=jobs.get_job)

    content_parser = subparsers.add_parser("content")
    content_subparsers = content_parser.add_subparsers(dest="content_command", required=True)
    content_list = content_subparsers.add_parser("list")
    content_list.add_argument("--limit", type=int, default=20)
    content_list.add_argument("--cursor")
    content_list.set_defaults(handler=content.list_content)
    content_get = content_subparsers.add_parser("get")
    content_get.add_argument("content_id", type=int)
    content_get.set_defaults(handler=content.get_content)
    content_submit = content_subparsers.add_parser("submit")
    content_submit.add_argument("--url", required=True)
    content_submit.add_argument("--note")
    content_submit.add_argument("--crawl-links", action="store_true")
    content_submit.add_argument("--subscribe-to-feed", action="store_true")
    add_wait_arguments(content_submit)
    content_submit.set_defaults(handler=content.submit_content)
    content_summarize = content_subparsers.add_parser("summarize")
    content_summarize.add_argument("--url", required=True)
    content_summarize.add_argument("--note")
    add_wait_arguments(content_summarize)
    content_summarize.set_defaults(handler=content.summarize_content)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--no-podcasts", action="store_true")
    search_parser.set_defaults(handler=search.search)

    onboarding_parser = subparsers.add_parser("onboarding")
    onboarding_subparsers = onboarding_parser.add_subparsers(
        dest="onboarding_command",
        required=True,
    )
    onboarding_run = onboarding_subparsers.add_parser("run")
    onboarding_run.add_argument("--brief", required=True)
    onboarding_run.add_argument("--seed-url", action="append", default=[])
    onboarding_run.add_argument("--seed-feed", action="append", default=[])
    onboarding_run.set_defaults(handler=onboarding.run_onboarding)
    onboarding_status = onboarding_subparsers.add_parser("status")
    onboarding_status.add_argument("run_id", type=int)
    onboarding_status.set_defaults(handler=onboarding.get_onboarding_status)
    onboarding_complete = onboarding_subparsers.add_parser("complete")
    onboarding_complete.add_argument("run_id", type=int)
    onboarding_complete.add_argument("--accept-all", action="store_true")
    onboarding_complete.add_argument("--source-id", action="append", type=int, default=[])
    onboarding_complete.add_argument("--subreddit", action="append", default=[])
    onboarding_complete.set_defaults(handler=onboarding.complete_onboarding)

    sources_parser = subparsers.add_parser("sources")
    sources_subparsers = sources_parser.add_subparsers(dest="sources_command", required=True)
    sources_list = sources_subparsers.add_parser("list")
    sources_list.add_argument("--type")
    sources_list.set_defaults(handler=sources.list_sources)
    sources_add = sources_subparsers.add_parser("add")
    sources_add.add_argument("--feed-url", required=True)
    sources_add.add_argument("--feed-type", required=True)
    sources_add.add_argument("--display-name")
    sources_add.set_defaults(handler=sources.add_source)

    digest_parser = subparsers.add_parser("digest")
    digest_subparsers = digest_parser.add_subparsers(dest="digest_command", required=True)
    digest_generate = digest_subparsers.add_parser("generate")
    digest_generate.add_argument("--start-at", required=True)
    digest_generate.add_argument("--end-at", required=True)
    digest_generate.add_argument("--form", choices=("short", "long"), default="short")
    add_wait_arguments(digest_generate)
    digest_generate.set_defaults(handler=digests.generate_digest)
    digest_list = digest_subparsers.add_parser("list")
    digest_list.add_argument("--limit", type=int, default=20)
    digest_list.add_argument("--cursor")
    digest_list.set_defaults(handler=digests.list_digests)

    return parser


def add_wait_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach standard client-side wait flags to a command."""
    parser.add_argument("--wait", action="store_true", help="Poll the returned job handle")
    parser.add_argument("--wait-interval", type=float, default=2.0)
    parser.add_argument("--wait-timeout", type=float, default=120.0)


def handle_set_server(_client: NewslyAgentClient | None, args: argparse.Namespace) -> dict:
    """Persist server URL in the CLI config."""
    config, path = update_config(server_url=args.server_url, path=args.config)
    return {
        "command": "config.set-server",
        "ok": True,
        "data": {"config_path": str(path), "server_url": config.server_url},
    }


def handle_set_api_key(_client: NewslyAgentClient | None, args: argparse.Namespace) -> dict:
    """Persist API key in the CLI config."""
    config, path = update_config(api_key=args.api_key, path=args.config)
    return {
        "command": "config.set-api-key",
        "ok": True,
        "data": {"config_path": str(path), "api_key_set": bool(config.api_key)},
    }


def build_client(args: argparse.Namespace) -> NewslyAgentClient:
    """Build an authenticated HTTP client from CLI config."""
    config = load_config(args.config)
    validate_runtime_config(config)
    return NewslyAgentClient(
        server_url=str(config.server_url),
        api_key=str(config.api_key),
        timeout_seconds=args.timeout,
    )


def validate_runtime_config(config: AgentCliConfig) -> None:
    """Ensure the CLI has the minimum config required for remote requests."""
    if not config.server_url:
        raise AgentClientError(
            "Missing server_url. Run `newsly-agent config set-server ...` first."
        )
    if not config.api_key:
        raise AgentClientError(
            "Missing api_key. Run `newsly-agent config set-api-key ...` first."
        )


def maybe_wait_for_job(
    client: NewslyAgentClient,
    envelope: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Optionally poll the server until an async job completes."""
    if not getattr(args, "wait", False):
        return envelope
    data = envelope.get("data")
    if not isinstance(data, dict):
        return envelope
    job_id = data.get("job_id") or data.get("task_id")
    if not isinstance(job_id, int):
        return envelope
    job = client.wait_for_job(
        job_id,
        WaitOptions(
            interval_seconds=args.wait_interval,
            timeout_seconds=args.wait_timeout,
        ),
    )
    updated = dict(envelope)
    updated["job"] = job
    return updated


def emit_output(envelope: dict[str, Any], output_format: str) -> None:
    """Render the command result in the selected output format."""
    if output_format == "json":
        print(json.dumps(envelope, indent=2, sort_keys=True))
        return

    print(f"command: {envelope.get('command')}")
    print(f"ok: {envelope.get('ok')}")
    print(json.dumps(envelope.get("data"), indent=2, sort_keys=True))
    if "job" in envelope:
        print("job:")
        print(json.dumps(envelope["job"], indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        client: NewslyAgentClient | None = None
        if args.command_group != "config":
            client = build_client(args)
        envelope = args.handler(client, args)
        if client is not None:
            envelope = maybe_wait_for_job(client, envelope, args)
        emit_output(envelope, args.output)
        return 0
    except AgentClientError as exc:
        error_envelope = {
            "command": getattr(args, "command_group", None),
            "ok": False,
            "error": {
                "message": str(exc),
                "status_code": exc.status_code,
                "payload": exc.payload,
            },
            "config_path": str(get_config_path(getattr(args, "config", None))),
        }
        emit_output(error_envelope, args.output)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
