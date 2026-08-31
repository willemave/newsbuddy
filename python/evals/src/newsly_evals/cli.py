"""Command-line entrypoint for the standalone eval island."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from newsly_evals.encoders import SentenceTransformerEncoder
from newsly_evals.relations import run_relation_eval


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run offline Newsly model evaluations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    relations = subparsers.add_parser("relations", help="Evaluate news relation embeddings")
    relations.add_argument("--cases", type=Path, required=True)
    relations.add_argument("--model", required=True)
    relations.add_argument("--output", type=Path, required=True)
    relations.add_argument("--primary", type=float, default=0.82)
    relations.add_argument("--secondary", type=float, default=0.72)
    relations.add_argument("--trace", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command != "relations":
        raise ValueError(f"unsupported command {args.command}")
    raw_payload: Any = json.loads(args.cases.read_text(encoding="utf-8"))
    raw_cases = raw_payload.get("cases") if isinstance(raw_payload, dict) else raw_payload
    if not isinstance(raw_cases, list):
        raise ValueError("cases file must contain a list or an object with a cases list")
    result = run_relation_eval(
        raw_cases=raw_cases,
        encoder=SentenceTransformerEncoder(args.model),
        thresholds=[
            {
                "label": "cli",
                "primary": args.primary,
                "secondary": args.secondary,
            }
        ],
        include_traces=args.trace,
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return int(any(run["summary"]["failed_count"] for run in result["runs"]))


if __name__ == "__main__":
    raise SystemExit(main())
