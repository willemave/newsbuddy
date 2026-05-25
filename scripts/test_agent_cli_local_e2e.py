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
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_ROOT = REPO_ROOT / "cli"
DEFAULT_STATE_DIR = REPO_ROOT / ".tmp" / "newsbuddy-local-smoke"


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
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    state_dir = args.state_dir.resolve()
    config_path = state_dir / "config.json"
    library_root = state_dir / "library"
    binary_path = state_dir / "newsbuddy"

    if args.skip_auth and args.fresh_auth:
        raise SmokeTestError("--skip-auth and --fresh-auth cannot be used together")

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

    if args.skip_auth:
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

    list_result = run_json(
        "content",
        "list",
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
        submitted_content_id = extract_content_id(submit_result)
        if submitted_content_id is None:
            raise SmokeTestError("submit step succeeded but did not return a content_id")
        run_json(
            "content",
            "get",
            str(submitted_content_id),
            step=f"Fetch submitted content detail for {submitted_content_id}",
        )

    print("== Local CLI smoke run completed successfully")
    print(f"   Config:  {config_path}")
    print(f"   Library: {library_root}")
    return 0


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
    first = contents[0]
    if not isinstance(first, dict):
        return None
    content_id = first.get("id")
    return content_id if isinstance(content_id, int) else None


def extract_content_id(payload: dict[str, Any]) -> int | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    content_id = data.get("content_id")
    return content_id if isinstance(content_id, int) else None


def run_subprocess(
    command: list[str],
    *,
    cwd: Path,
    capture: bool,
) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(command))
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
            f"command failed with exit code {completed.returncode}: {' '.join(command)}"
        )
    return completed


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeTestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
