#!/usr/bin/env python3
"""Helpers for resolving and validating supervisor program status output."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def parse_supervisor_status(status_output: str) -> list[tuple[str, str]]:
    """Parse `supervisorctl status` output into `(name, status)` rows."""
    rows: list[tuple[str, str]] = []

    for raw_line in status_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        rows.append((parts[0], parts[1]))

    return rows


def _matches_configured_program(actual_name: str, configured_name: str) -> bool:
    """Return whether an actual supervisor entry matches a configured program name."""
    return actual_name == configured_name or actual_name.startswith(f"{configured_name}:")


def resolve_program_names(
    configured_programs: Sequence[str],
    status_output: str,
) -> list[str]:
    """Resolve configured supervisor names to actual status entry names."""
    status_rows = parse_supervisor_status(status_output)
    resolved: list[str] = []
    seen: set[str] = set()

    for configured_name in configured_programs:
        for actual_name, _status in status_rows:
            if not _matches_configured_program(actual_name, configured_name):
                continue
            if actual_name in seen:
                continue
            seen.add(actual_name)
            resolved.append(actual_name)

    return resolved


def list_missing_running_programs(
    required_programs: Sequence[str],
    status_output: str,
) -> list[str]:
    """Return required programs that are missing or not fully RUNNING."""
    status_rows = parse_supervisor_status(status_output)
    missing: list[str] = []

    for required_name in required_programs:
        matching_statuses = [
            status
            for actual_name, status in status_rows
            if _matches_configured_program(actual_name, required_name)
        ]
        if not matching_statuses:
            missing.append(required_name)
            continue
        if any(status != "RUNNING" for status in matching_statuses):
            missing.append(required_name)

    return missing


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Resolve or validate supervisorctl status output",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Resolve configured program names to actual supervisor entry names",
    )
    resolve_parser.add_argument(
        "--configured",
        nargs="+",
        required=True,
        help="Configured program names to resolve",
    )

    missing_parser = subparsers.add_parser(
        "missing",
        help="List required programs that are not fully RUNNING",
    )
    missing_parser.add_argument(
        "--required",
        nargs="+",
        required=True,
        help="Required program names to validate",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the supervisor helper CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    status_output = sys.stdin.read()

    if args.command == "resolve":
        print(" ".join(resolve_program_names(args.configured, status_output)))
        return 0

    if args.command == "missing":
        print(" ".join(list_missing_running_programs(args.required, status_output)))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
