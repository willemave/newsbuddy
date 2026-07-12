#!/usr/bin/env python3
"""Set up, inspect, and open Newsly's stable local developer user."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.db import get_db, init_db  # noqa: E402
from scripts.support.dev_user import (  # noqa: E402
    ONBOARDING_STATES,
    dev_user_status,
    find_showcase_user,
    setup_onboarding_user,
    setup_showcase_user,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Idempotently seed the developer user.")
    setup.add_argument(
        "--profile",
        choices=("showcase", "onboarding"),
        default="showcase",
        help="Seed rich everyday data or a deterministic onboarding state.",
    )
    setup.add_argument(
        "--state",
        choices=ONBOARDING_STATES,
        default="early",
        help="Start Here state used by the onboarding profile (default: early).",
    )
    setup.add_argument(
        "--briefing-mode",
        choices=("llm", "deterministic", "none"),
        default="deterministic",
        help="How to compose the seeded Briefing (default: deterministic).",
    )
    setup.add_argument("--launch", action="store_true", help="Log in on a booted simulator.")
    _add_simulator_options(setup)

    status = subparsers.add_parser("status", help="Inspect the developer user and Briefing.")
    status.set_defaults(command="status")

    login = subparsers.add_parser("login", help="Log in as the developer user on Simulator.")
    _add_simulator_options(login)
    return parser.parse_args()


def _add_simulator_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--simulator",
        default="booted",
        help="Simulator UDID, or 'booted' to use the first booted device.",
    )
    parser.add_argument(
        "--server-url",
        default="http://localhost:8000",
        help="Local Newsly API used by the simulator.",
    )


def main() -> int:
    args = parse_args()
    init_db()
    with get_db() as db:
        if args.command == "setup":
            if args.profile == "onboarding":
                result = setup_onboarding_user(db, state=args.state)
            else:
                result = setup_showcase_user(db, briefing_mode=args.briefing_mode)
        else:
            user = find_showcase_user(db)
            if user is None:
                raise SystemExit("Developer user not found. Run `scripts/dev_user.py setup` first.")
            result = dev_user_status(db, user=user)

    if args.command == "login" or (args.command == "setup" and args.launch):
        _open_debug_login(
            user_id=int(result["user"]["id"]),
            simulator=args.simulator,
            server_url=args.server_url,
        )
        result["simulator_login"] = "requested"

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        _print_status(result)
    return 0


def _open_debug_login(*, user_id: int, simulator: str, server_url: str) -> None:
    udid = _booted_simulator_udid() if simulator == "booted" else simulator
    parsed = urlparse(server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SystemExit(f"Invalid --server-url: {server_url}")
    query = urlencode(
        {
            "user_id": user_id,
            "host": parsed.hostname,
            "port": parsed.port or (443 if parsed.scheme == "https" else 80),
            "https": str(parsed.scheme == "https").lower(),
        }
    )
    result = subprocess.run(
        ["xcrun", "simctl", "openurl", udid, f"newsly://debug-login?{query}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown simctl error"
        raise SystemExit(f"Unable to open Newsly debug login on simulator {udid}: {detail}")


def _booted_simulator_udid() -> str:
    result = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "booted", "-j"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "Unable to list booted simulators.")
    payload = json.loads(result.stdout)
    for devices in payload.get("devices", {}).values():
        for device in devices:
            if device.get("state") == "Booted" and device.get("isAvailable", True):
                return str(device["udid"])
    raise SystemExit("No booted simulator found. Boot one in Xcode first.")


def _print_status(result: dict[str, Any]) -> None:
    user = result["user"]
    content = result["content"]
    briefing = result["briefing"]
    counts = briefing["counts"]
    print(f"Developer user {user['id']}: {user['email']} ({result['profile']})")
    print(
        "Content: "
        f"{content['articles']} articles, {content['podcasts']} podcasts, "
        f"{content['news']} news, {content['read']} read, {content['saved']} saved"
    )
    print(
        f"Briefing: version {briefing['version']}, {len(briefing['lenses'])} lenses, "
        f"{counts.get('segments', 0)} segments, {counts.get('pending', 0)} pending, "
        f"{counts.get('degraded', 0)} degraded"
    )
    for lens in briefing["lenses"]:
        print(
            f"  - {lens['title']}: {lens['unread_sources']} unread "
            f"across {lens['segments']} segment(s)"
        )
    latest_task = briefing.get("latest_task")
    if latest_task:
        print(
            "Latest refresh task: "
            f"{latest_task['id']} {latest_task['status']} mode={latest_task['mode']}"
        )
        if latest_task.get("error"):
            print(f"  Error: {latest_task['error']}")
    if onboarding := result.get("onboarding"):
        print(
            f"Onboarding: {onboarding['state']}, "
            f"{onboarding.get('completed_sources', 0)} completed source(s)"
        )
    if result.get("simulator_login"):
        print("Simulator login requested.")


if __name__ == "__main__":
    raise SystemExit(main())
