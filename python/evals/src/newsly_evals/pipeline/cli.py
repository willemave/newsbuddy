"""Local pipeline fixture generation and evaluated runs."""

from __future__ import annotations

import argparse
from pathlib import Path

from newsly_evals.chat.schema import read_yaml
from newsly_evals.pipeline.generator import generate_sql
from newsly_evals.pipeline.runner import run
from newsly_evals.pipeline.schema import Suite


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("pipeline", help="Black-box summary and Briefing evals")
    parser.add_argument("action", choices=["generate", "run"])
    parser.add_argument("suite", type=Path)
    parser.add_argument("--case", action="append", default=[], dest="cases")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--namespace", default="pipeline-eval")
    parser.add_argument("--binary-dir", type=Path, default=Path("rust/target/debug"))
    parser.add_argument("--postgres-url", default="postgresql://localhost/postgres")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model", default="openai:gpt-5.6-terra")
    parser.add_argument("--judge-model", default="gpt-5.6-sol")
    parser.add_argument("--timeout", type=float, default=180)


def execute(args: argparse.Namespace) -> int:
    suite = Suite.model_validate(read_yaml(args.suite))
    if set(args.cases) - {c.id for c in suite.cases}:
        raise ValueError("unknown case ID")
    cases = [c for c in suite.cases if not args.cases or c.id in args.cases]
    if args.action == "generate":
        if len(cases) != 1:
            raise ValueError("generate requires exactly one --case")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(generate_sql(cases[0], args.namespace))
        return 0
    if not 0 < args.timeout <= 600:
        raise ValueError("timeout must be in (0, 600]")
    return run(
        cases,
        output=args.output,
        binary_dir=args.binary_dir,
        postgres_url=args.postgres_url,
        env_file=args.env_file,
        model=args.model,
        judge_model=args.judge_model,
        timeout=args.timeout,
    )
