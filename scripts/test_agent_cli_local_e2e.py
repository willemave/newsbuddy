#!/usr/bin/env python3
"""Smoke-test the local Newsbuddy CLI against a local backend."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_ROOT = REPO_ROOT / "cli"
DEFAULT_STATE_DIR = REPO_ROOT / ".tmp" / "newsbuddy-local-smoke"

sys.path.insert(0, str(REPO_ROOT))


class SmokeTestError(RuntimeError):
    """Raised when one smoke-test step fails."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server",
        default="http://localhost:8000",
        help="Newsly server base URL.",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE_DIR,
        help="Directory for the built binary, config, and local library sync output.",
    )
    parser.add_argument(
        "--device-name",
        default="Codex Local Smoke",
        help="Device name shown during CLI QR auth.",
    )
    parser.add_argument(
        "--content-limit",
        type=int,
        default=5,
        help="How many content cards to request during the smoke run.",
    )
    parser.add_argument(
        "--auth-poll-interval",
        default="2s",
        help="CLI auth polling interval passed to `auth login`.",
    )
    parser.add_argument(
        "--auth-timeout",
        default="10m",
        help="Maximum time to wait for QR approval.",
    )
    parser.add_argument(
        "--submit-url",
        help="Optional URL to submit and wait on after auth succeeds.",
    )
    parser.add_argument(
        "--submit-wait-timeout",
        default="5m",
        help="Maximum time to wait for the optional submit job.",
    )
    parser.add_argument(
        "--cli-timeout",
        default="60s",
        help="HTTP timeout passed to each CLI command during the smoke run.",
    )
    parser.add_argument(
        "--health-timeout-seconds",
        type=float,
        default=5.0,
        help="Per-request timeout when probing the server health endpoint.",
    )
    parser.add_argument(
        "--health-retries",
        type=int,
        default=3,
        help="How many health-check attempts to make before failing.",
    )
    parser.add_argument(
        "--health-retry-delay-seconds",
        type=float,
        default=1.0,
        help="Delay between health-check attempts.",
    )
    parser.add_argument(
        "--skip-auth",
        action="store_true",
        help="Reuse the existing API key in the smoke config instead of starting QR auth.",
    )
    parser.add_argument(
        "--fresh-auth",
        action="store_true",
        help="Delete any prior smoke config before authenticating.",
    )
    parser.add_argument(
        "--auto-debug-auth",
        action="store_true",
        help=(
            "Create a debug user and approve a CLI link through localhost APIs. "
            "Requires the local server to run in development/debug mode."
        ),
    )
    parser.add_argument(
        "--seed-test-data",
        action="store_true",
        help=(
            "Run scripts/generate_test_data.py for the authenticated/debug user "
            "before exercising commands."
        ),
    )
    parser.add_argument(
        "--seed-user-id",
        type=int,
        help="User id to pass to generate_test_data.py when not using --auto-debug-auth.",
    )
    parser.add_argument(
        "--exercise-all",
        action="store_true",
        help="Exercise the full local CLI command surface after auth and optional seeding.",
    )
    parser.add_argument(
        "--summarize-url",
        help="Optional URL to summarize during --exercise-all.",
    )
    parser.add_argument(
        "--feed-url",
        default="https://lucumr.pocoo.org/feed.atom",
        help="Real RSS/Atom feed URL used by sources add during --exercise-all.",
    )
    parser.add_argument(
        "--run-onboarding-start",
        action="store_true",
        help=(
            "Also run onboarding start during --exercise-all. "
            "This can invoke live LLM/search providers."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    state_dir = args.state_dir.resolve()
    config_path = state_dir / "config.json"
    library_root = state_dir / "library"
    binary_path = state_dir / "newsbuddy"

    if args.skip_auth and args.fresh_auth:
        raise SmokeTestError("--skip-auth and --fresh-auth cannot be used together")
    if args.skip_auth and args.auto_debug_auth:
        raise SmokeTestError("--skip-auth and --auto-debug-auth cannot be used together")
    if args.seed_test_data and not args.auto_debug_auth and args.seed_user_id is None:
        raise SmokeTestError("--seed-test-data requires --auto-debug-auth or --seed-user-id")

    if args.fresh_auth and state_dir.exists():
        shutil.rmtree(state_dir)

    state_dir.mkdir(parents=True, exist_ok=True)
    library_root.mkdir(parents=True, exist_ok=True)

    check_server_health(
        args.server,
        timeout_seconds=args.health_timeout_seconds,
        retries=args.health_retries,
        retry_delay_seconds=args.health_retry_delay_seconds,
    )
    build_cli(binary_path)

    def run_json(*cli_args: str, step: str) -> dict[str, Any]:
        return run_cli_json(
            binary_path,
            config_path,
            args.cli_timeout,
            *cli_args,
            step=step,
        )

    def run_stream(*cli_args: str, step: str, failure_hint: str | None = None) -> None:
        run_cli_streaming(
            binary_path,
            config_path,
            args.cli_timeout,
            *cli_args,
            step=step,
            failure_hint=failure_hint,
        )

    run_json(
        "config",
        "set",
        "server",
        args.server,
        step="Persist local server URL",
    )
    run_json(
        "config",
        "set",
        "library-root",
        str(library_root),
        step="Persist local library root",
    )

    debug_user: DebugAuthContext | None = None
    if args.auto_debug_auth:
        debug_user = create_debug_cli_auth(args.server, args.device_name)
        run_json(
            "config",
            "set",
            "api-key",
            debug_user.api_key,
            step="Persist debug API key",
        )
    elif args.skip_auth:
        print("== Reusing existing CLI auth from smoke config")
    else:
        run_stream(
            "auth",
            "login",
            "--device-name",
            args.device_name,
            "--poll-interval",
            args.auth_poll_interval,
            "--poll-timeout",
            args.auth_timeout,
            step="Authenticate with the local app",
            failure_hint=(
                "Approve the QR link in the Newsly app, or rerun with "
                "--skip-auth if the config already has a valid key."
            ),
        )

    config_result = run_json(
        "config",
        "show",
        step="Verify CLI config",
    )
    if not bool(config_result["data"].get("api_key_set")):
        raise SmokeTestError("CLI config is missing an API key after auth")

    seed_user_id = debug_user.user_id if debug_user else args.seed_user_id
    if args.seed_test_data and seed_user_id is not None:
        seed_local_test_data(seed_user_id)

    list_result = run_json(
        "content",
        "list",
        "--content-type",
        "article",
        "--limit",
        str(args.content_limit),
        step="Fetch content list",
    )

    first_content_id = extract_first_content_id(list_result)
    if first_content_id is not None:
        run_json(
            "content",
            "get",
            str(first_content_id),
            step=f"Fetch content detail for {first_content_id}",
        )
    else:
        print("== Content detail step skipped because the list response had no items")

    run_json(
        "sources",
        "list",
        step="Fetch source subscriptions",
    )
    run_json(
        "library",
        "sync",
        step="Sync personal markdown library",
    )

    if args.submit_url:
        submit_result = run_json(
            "content",
            "submit",
            args.submit_url,
            "--wait",
            "--wait-timeout",
            args.submit_wait_timeout,
            step=f"Submit content for processing: {args.submit_url}",
        )
        if maybe_fetch_completed_content_detail(run_json, submit_result) is None:
            raise SmokeTestError("submit step succeeded but did not return a content_id")

    if args.exercise_all:
        run_full_cli_sweep(
            args,
            binary_path=binary_path,
            config_path=config_path,
            cli_timeout=args.cli_timeout,
            first_content_id=first_content_id,
            debug_user=debug_user,
        )

    print("== Local CLI smoke run completed successfully")
    print(f"   Config:  {config_path}")
    print(f"   Library: {library_root}")
    return 0


@dataclass(frozen=True)
class DebugAuthContext:
    user_id: int
    api_key: str


def create_debug_cli_auth(server_url: str, device_name: str) -> DebugAuthContext:
    print("== Creating debug user and approving local CLI link")
    debug_response = request_json(
        server_url,
        "POST",
        "/auth/debug/new-user",
        payload={
            "has_completed_onboarding": True,
            "has_completed_new_user_tutorial": True,
        },
    )
    access_token = require_string(debug_response, "access_token")
    user = debug_response.get("user")
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise SmokeTestError("debug auth response did not include a numeric user.id")
    user_id = user["id"]

    link = request_json(
        server_url,
        "POST",
        "/api/agent/cli/link/start",
        payload={"device_name": device_name},
    )
    session_id = require_string(link, "session_id")
    poll_token = require_string(link, "poll_token")
    approve_url = require_string(link, "approve_url")
    approve_token = extract_approve_token(approve_url)
    request_json(
        server_url,
        "POST",
        f"/api/agent/cli/link/{session_id}/approve",
        payload={"approve_token": approve_token, "device_name": device_name},
        bearer_token=access_token,
    )
    polled = request_json(
        server_url,
        "GET",
        f"/api/agent/cli/link/{session_id}?{urlencode({'poll_token': poll_token})}",
    )
    api_key = require_string(polled, "api_key")
    print(f"   Debug user id: {user_id}")
    return DebugAuthContext(user_id=user_id, api_key=api_key)


def seed_local_test_data(user_id: int) -> None:
    print(f"== Seeding local test data for user {user_id}")
    run_subprocess(
        [
            sys.executable,
            "scripts/generate_test_data.py",
            "--articles",
            "4",
            "--podcasts",
            "2",
            "--news",
            "6",
            "--news-days-back",
            "2",
            "--no-pending",
            "--article-summary-format",
            "structured",
            "--podcast-summary-format",
            "structured",
            "--user-ids",
            str(user_id),
        ],
        cwd=REPO_ROOT,
        capture=False,
    )


def run_full_cli_sweep(
    args: argparse.Namespace,
    *,
    binary_path: Path,
    config_path: Path,
    cli_timeout: str,
    first_content_id: int | None,
    debug_user: DebugAuthContext | None,
) -> None:
    print("== Exercising full local CLI command surface")

    def run_json(*cli_args: str, step: str) -> dict[str, Any]:
        return run_cli_json(
            binary_path,
            config_path,
            cli_timeout,
            *cli_args,
            step=step,
        )

    run_json("version", step="Fetch CLI version")
    run_json("config", "show", step="Show persisted config")
    run_json(
        "content",
        "submissions",
        "list",
        "--limit",
        "5",
        step="List content submission statuses",
    )
    run_json(
        "content",
        "list",
        "--content-type",
        "article",
        "--limit",
        str(args.content_limit),
        step="Fetch article content list",
    )

    if first_content_id is not None and debug_user is not None:
        save_content_to_knowledge(args.server, first_content_id, debug_user.api_key)
        run_json("library", "sync", step="Sync personal markdown library with saved content")

    submit_url = args.submit_url or unique_example_url("codex-cli-submit")
    submit_result = run_json(
        "content",
        "submit",
        submit_url,
        "--title",
        "Codex CLI live submit smoke",
        "--content-type",
        "article",
        step=f"Submit content: {submit_url}",
    )
    maybe_fetch_completed_content_detail(run_json, submit_result)
    job_id = extract_job_id(submit_result)
    seeded_job_id = seed_completed_task()
    run_json("jobs", "get", str(job_id or seeded_job_id), step="Fetch job status")
    run_json(
        "jobs",
        "wait",
        str(seeded_job_id),
        "--wait-interval",
        "100ms",
        "--wait-timeout",
        "2s",
        step="Wait on a completed local job",
    )

    summarize_url = args.summarize_url or unique_example_url("codex-cli-summarize")
    run_json(
        "content",
        "summarize",
        summarize_url,
        "--title",
        "Codex CLI live summarize smoke",
        "--content-type",
        "article",
        step=f"Summarize content: {summarize_url}",
    )
    run_json(
        "search",
        "ai agents",
        "--limit",
        "2",
        "--include-podcasts=false",
        step="Run external search",
    )

    run_json("sources", "list", step="List source subscriptions")
    run_json(
        "sources",
        "add",
        args.feed_url,
        "--feed-type",
        "atom",
        "--display-name",
        f"Codex CLI Smoke Feed {int(time.time())}",
        step="Subscribe to a real Atom feed",
    )
    run_json("sources", "list", "--type", "atom", step="List Atom source subscriptions")

    news_list = run_json(
        "news",
        "list",
        "--limit",
        "5",
        "--read-filter",
        "unread",
        step="Fetch unread news list",
    )
    news_item_id = extract_first_news_item_id(news_list)
    if news_item_id is not None:
        run_json("news", "get", str(news_item_id), step=f"Fetch news item {news_item_id}")
        run_json("news", "mark-read", str(news_item_id), step=f"Mark news item {news_item_id} read")
        run_json(
            "news",
            "list",
            "--limit",
            "5",
            "--read-filter",
            "read",
            step="Fetch read news list",
        )
        run_json("news", "convert", str(news_item_id), step=f"Convert news item {news_item_id}")
    else:
        print("== News detail/mark-read/convert skipped because no news items were returned")

    if args.run_onboarding_start:
        start_result = run_json(
            "onboarding",
            "start",
            "--brief",
            "I follow AI infrastructure, software engineering, and developer tools.",
            step="Start onboarding discovery",
        )
        run_id = extract_run_id(start_result)
        if run_id is not None:
            run_json("onboarding", "status", str(run_id), step=f"Fetch onboarding status {run_id}")
    else:
        onboarding_user_id = debug_user.user_id if debug_user else None
        onboarding_run_id = seed_completed_onboarding_run(onboarding_user_id)
        if onboarding_run_id is not None:
            run_json(
                "onboarding",
                "status",
                str(onboarding_run_id),
                step=f"Fetch seeded onboarding status {onboarding_run_id}",
            )
            run_json(
                "onboarding",
                "complete",
                str(onboarding_run_id),
                "--accept-all",
                step=f"Complete seeded onboarding run {onboarding_run_id}",
            )
        else:
            print("== Onboarding status/complete skipped because no debug user id was available")

    run_subprocess(
        build_cli_command(binary_path, config_path, cli_timeout, "--help"),
        cwd=REPO_ROOT,
        capture=True,
    )
    run_subprocess(
        build_cli_command(binary_path, config_path, cli_timeout, "completion", "bash"),
        cwd=REPO_ROOT,
        capture=True,
    )


def check_server_health(
    server_url: str,
    *,
    timeout_seconds: float,
    retries: int,
    retry_delay_seconds: float,
) -> None:
    health_url = f"{server_url.rstrip('/')}/health"
    print(f"== Checking local server health at {health_url}")
    request = urllib.request.Request(health_url, headers={"Accept": "application/json"})
    if retries <= 0:
        raise SmokeTestError("health-retries must be greater than zero")

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise SmokeTestError(f"health check returned HTTP {response.status}")
                return
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == retries:
                break
            print(
                "   Health probe failed "
                f"(attempt {attempt}/{retries}): {exc}. Retrying in {retry_delay_seconds:.1f}s..."
            )
            time.sleep(retry_delay_seconds)

    raise SmokeTestError(
        f"local server is not reachable at {server_url} after {retries} attempts. "
        "Start it first, for example with `./scripts/start_server.sh`."
    ) from last_error


def request_json(
    server_url: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    bearer_token: str | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    url = f"{server_url.rstrip('/')}/{path.lstrip('/')}"
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SmokeTestError(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SmokeTestError(f"{method} {path} failed: {exc}") from exc
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeTestError(f"{method} {path} returned non-JSON response") from exc
    if not isinstance(decoded, dict):
        raise SmokeTestError(f"{method} {path} returned a non-object JSON response")
    return decoded


def require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SmokeTestError(f"response did not include required string field `{key}`")
    return value


def extract_approve_token(approve_url: str) -> str:
    parsed = urlparse(approve_url)
    values = parse_qs(parsed.query).get("approve_token", [])
    if not values or not values[0]:
        raise SmokeTestError("CLI approve URL did not include approve_token")
    return values[0]


def build_cli(binary_path: Path) -> None:
    print(f"== Building local CLI to {binary_path}")
    run_subprocess(
        ["go", "build", "-o", str(binary_path), "./cmd/newsbuddy"],
        cwd=CLI_ROOT,
        capture=False,
    )


def build_cli_command(
    binary_path: Path,
    config_path: Path,
    cli_timeout: str,
    *args: str,
) -> list[str]:
    return [
        str(binary_path),
        "--config",
        str(config_path),
        "--timeout",
        cli_timeout,
        "--output",
        "json",
        *args,
    ]


def run_cli_json(
    binary_path: Path,
    config_path: Path,
    cli_timeout: str,
    *args: str,
    step: str,
) -> dict[str, Any]:
    print(f"== {step}")
    command = build_cli_command(binary_path, config_path, cli_timeout, *args)
    completed = run_subprocess(command, cwd=REPO_ROOT, capture=True)
    if completed.stderr:
        stderr = completed.stderr.strip()
        if stderr:
            print(stderr)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeTestError(f"failed to parse CLI JSON output for `{step}`") from exc
    if not payload.get("ok", False):
        message = payload.get("error", {}).get("message", "unknown error")
        raise SmokeTestError(f"`{step}` failed: {message}")
    return payload


def run_cli_streaming(
    binary_path: Path,
    config_path: Path,
    cli_timeout: str,
    *args: str,
    step: str,
    failure_hint: str | None = None,
) -> None:
    print(f"== {step}")
    command = build_cli_command(binary_path, config_path, cli_timeout, *args)
    try:
        run_subprocess(command, cwd=REPO_ROOT, capture=False)
    except SmokeTestError as exc:
        if failure_hint:
            raise SmokeTestError(f"{exc}\n{failure_hint}") from exc
        raise


def extract_first_content_id(payload: dict[str, Any]) -> int | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    contents = data.get("contents")
    if not isinstance(contents, list) or not contents:
        return None
    for item in contents:
        if not isinstance(item, dict):
            continue
        if item.get("content_type") == "news":
            continue
        content_id = item.get("id")
        if isinstance(content_id, int):
            return content_id
    return None


def extract_content_id(payload: dict[str, Any]) -> int | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    content_id = data.get("content_id")
    return content_id if isinstance(content_id, int) else None


def maybe_fetch_completed_content_detail(
    run_json: Callable[..., dict[str, Any]],
    submit_result: dict[str, Any],
) -> int | None:
    submitted_content_id = extract_content_id(submit_result)
    if submitted_content_id is None:
        return None
    submitted_status = extract_status(submit_result)
    if submitted_status == "completed":
        run_json(
            "content",
            "get",
            str(submitted_content_id),
            step=f"Fetch submitted content detail for {submitted_content_id}",
        )
        return submitted_content_id
    print(
        "== Submitted content detail skipped because "
        f"content {submitted_content_id} is {submitted_status or 'not completed'}"
    )
    return submitted_content_id


def extract_job_id(payload: dict[str, Any]) -> int | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    for key in ("task_id", "job_id"):
        value = data.get(key)
        if isinstance(value, int):
            return value
    return None


def extract_status(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    return status if isinstance(status, str) else None


def extract_run_id(payload: dict[str, Any]) -> int | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    run_id = data.get("run_id")
    return run_id if isinstance(run_id, int) else None


def extract_first_news_item_id(payload: dict[str, Any]) -> int | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    contents = data.get("contents")
    if not isinstance(contents, list) or not contents:
        return None
    first = contents[0]
    if not isinstance(first, dict):
        return None
    item_id = first.get("id")
    return item_id if isinstance(item_id, int) else None


def unique_example_url(prefix: str) -> str:
    return f"https://example.com/{prefix}-{int(time.time() * 1000)}"


def save_content_to_knowledge(server_url: str, content_id: int, api_key: str) -> None:
    print(f"== Saving content {content_id} to knowledge for library sync")
    request_json(
        server_url,
        "POST",
        f"/api/content/{content_id}/knowledge",
        bearer_token=api_key,
    )


def seed_completed_task() -> int:
    print("== Seeding completed local processing task")
    from app.core.db import get_session_factory, init_db
    from app.models.contracts import TaskQueue, TaskStatus, TaskType
    from app.models.db import ProcessingTask

    init_db()
    now = datetime.now(UTC).replace(tzinfo=None)
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        task = ProcessingTask(
            task_type=TaskType.PROCESS_CONTENT.value,
            status=TaskStatus.COMPLETED.value,
            queue_name=TaskQueue.CONTENT.value,
            payload={"source": "scripts/test_agent_cli_local_e2e.py"},
            created_at=now,
            available_at=now,
            started_at=now,
            completed_at=now,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        if not isinstance(task.id, int):
            raise SmokeTestError("seeded processing task did not receive an id")
        return task.id


def seed_completed_onboarding_run(user_id: int | None) -> int | None:
    if user_id is None:
        return None
    print(f"== Seeding completed onboarding run for user {user_id}")
    from app.core.db import get_session_factory, init_db
    from app.models.db import (
        OnboardingDiscoveryLane,
        OnboardingDiscoveryRun,
        OnboardingDiscoverySuggestion,
    )

    init_db()
    now = datetime.now(UTC).replace(tzinfo=None)
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        run = OnboardingDiscoveryRun(
            user_id=user_id,
            status="completed",
            topic_summary="AI infrastructure and developer tools",
            inferred_topics=["ai", "infrastructure", "developer tools"],
            lane_summary="Local CLI smoke test onboarding lane",
            created_at=now,
            completed_at=now,
        )
        db.add(run)
        db.flush()
        db.add(
            OnboardingDiscoveryLane(
                run_id=run.id,
                lane_name="Developer Tools",
                goal="Find reliable developer-tooling feeds",
                target="atom",
                status="completed",
                query_count=1,
                completed_queries=1,
                queries=["developer tools engineering blogs"],
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            OnboardingDiscoverySuggestion(
                run_id=run.id,
                user_id=user_id,
                suggestion_type="atom",
                site_url="https://lucumr.pocoo.org/",
                feed_url="https://lucumr.pocoo.org/feed.atom",
                title="Armin Ronacher",
                description="Engineering writing used by the CLI smoke test.",
                rationale="Stable Atom feed for local CLI endpoint coverage.",
                status="new",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
        db.refresh(run)
        return run.id if isinstance(run.id, int) else None


def run_subprocess(
    command: list[str],
    *,
    cwd: Path,
    capture: bool,
) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(redact_command(command)))
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=capture,
        check=False,
    )
    if completed.returncode != 0:
        if capture:
            if completed.stdout.strip():
                print(completed.stdout.strip())
            if completed.stderr.strip():
                print(completed.stderr.strip(), file=sys.stderr)
        raise SmokeTestError(
            "command failed with exit code "
            f"{completed.returncode}: {' '.join(redact_command(command))}"
        )
    return completed


def redact_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for arg in command:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        if arg == "--api-key" or arg.startswith("--api-key=") or arg.startswith("newsly_ak_"):
            if arg == "--api-key":
                redacted.append(arg)
                redact_next = True
            elif arg.startswith("--api-key="):
                redacted.append("--api-key=<redacted>")
            else:
                redacted.append("<redacted-api-key>")
            continue
        redacted.append(arg)
    return redacted


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeTestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
