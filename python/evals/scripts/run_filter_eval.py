#!/usr/bin/env python3
"""Prepare, execute, and score a frozen offline Jev filtering ablation."""

import argparse
import asyncio
import json
from pathlib import Path

from newsly_evals.filtering.artifacts import load_manifest, prepare
from newsly_evals.filtering.judge import inspect_halts, prepare_judge, run_judge
from newsly_evals.filtering.metrics import score
from newsly_evals.filtering.migration import migrate_openrouter
from newsly_evals.filtering.provider import run_jev
from newsly_evals.filtering.recovery import acknowledge_jev, revalidate_jev


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--sample", type=Path, required=True)
    preparation.add_argument("--profiles", type=Path, required=True)
    preparation.add_argument("--output", type=Path, required=True)
    migration = commands.add_parser("migrate-openrouter")
    migration.add_argument("--source", type=Path, required=True)
    migration.add_argument("--output", type=Path, required=True)
    migration.add_argument("--allocation", choices=("smoke", "main"), required=True)
    for recovery_name in ("revalidate-jev", "acknowledge-jev"):
        recovery = commands.add_parser(recovery_name)
        recovery.add_argument("--output", type=Path, required=True)
        recovery.add_argument("--attempt-index", type=int, required=True)
    for name in ("run-jev", "prepare-judge", "run-judge", "inspect-judge-halts", "score"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
        if name == "run-jev":
            command.add_argument("--limit", type=int)
            command.add_argument("--dollar-cap", type=float, required=True)
            command.add_argument("--request-cap", type=int, required=True)
            command.add_argument("--concurrency", type=int, default=4)
        if name == "run-judge":
            command.add_argument("--limit", type=int)
            command.add_argument("--max-calls", type=int, required=True)
            command.add_argument("--audit", action="store_true")
            command.add_argument("--acknowledge-halt", action="append", default=[])
            command.add_argument("--revalidate-halted", action="append", default=[])
            command.add_argument("--revalidate-only", action="store_true")
            command.add_argument("--concurrency", type=int, default=2)
        if name == "score":
            command.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    if args.command == "migrate-openrouter":
        result = migrate_openrouter(args.source, args.output, args.allocation)
        print(
            json.dumps(
                {
                    "manifest_hash": result["manifest_hash"],
                    "reference_source": result["reference_source"]["descriptor_hash"],
                    "jobs": len(result["jobs"]),
                }
            )
        )
        return
    if args.command == "prepare":
        result = prepare(args.sample, args.profiles, args.output)
        print(
            json.dumps(
                {
                    "manifest_hash": result["manifest_hash"],
                    "pairs": len(result["pairs"]),
                    "jobs": len(result["jobs"]),
                    "profile_counts": result["profile_counts"],
                }
            )
        )
        return
    manifest = load_manifest(args.output / "manifest.json")
    if args.command == "acknowledge-jev":
        result = acknowledge_jev(manifest, args.output, args.attempt_index)
    elif args.command == "revalidate-jev":
        result = revalidate_jev(manifest, args.output, args.attempt_index)
    elif args.command == "run-jev":
        result = asyncio.run(
            run_jev(
                manifest,
                args.output,
                limit=args.limit,
                dollar_cap=args.dollar_cap,
                request_cap=args.request_cap,
                concurrency=args.concurrency,
            )
        )
    elif args.command == "prepare-judge":
        prepare_judge(manifest, args.output)
        result = {"prepared": True}
    elif args.command == "inspect-judge-halts":
        result = inspect_halts(manifest, args.output)
    elif args.command == "run-judge":
        result = run_judge(
            manifest,
            args.output,
            args.max_calls,
            args.limit,
            audit=args.audit,
            concurrency=args.concurrency,
            acknowledge_halt=args.acknowledge_halt,
            revalidate_halted=args.revalidate_halted,
            revalidate_only=args.revalidate_only,
        )
    else:
        result = score(manifest, args.output, allow_partial=args.allow_partial)
        result = {
            "report": str(args.output / "metrics.json"),
            "selection": result["selection"],
            "provisional": result["provisional"],
        }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
