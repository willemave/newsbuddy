"""CLI for generation, local runs, and judging saved answers without another candidate call."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from newsly_evals.chat.generator import generate_sql
from newsly_evals.chat.judge import run_judge
from newsly_evals.chat.runner import run_suite
from newsly_evals.chat.schema import Scenario, read_yaml


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("chat", help="Local black-box chat eval harness")
    actions = parser.add_subparsers(dest="chat_action", required=True)
    generate = actions.add_parser("generate", help="Compile a YAML scenario to SQL; no model calls")
    generate.add_argument("scenario", type=Path)
    generate.add_argument("--namespace", default="local-eval")
    generate.add_argument("--output", type=Path, required=True)
    run = actions.add_parser("run", help="Bootstrap local Newsly, run cases, judge and report")
    run.add_argument("suite", type=Path)
    run.add_argument("--binary-dir", type=Path, default=Path("rust/target/debug"))
    run.add_argument("--postgres-url", default="postgresql://localhost/postgres")
    run.add_argument("--env-file", type=Path)
    run.add_argument("--model", required=True, help="Candidate provider:model")
    run.add_argument("--judge-model", required=True, help="Locally authenticated Codex CLI model")
    run.add_argument("--case", action="append", default=[], dest="cases")
    run.add_argument("--timeout", type=float, default=180)
    run.add_argument("--max-turns", type=int, default=20)
    run.add_argument(
        "--keep-database", action="store_true", help="Keep DB after stopping processes"
    )
    run.add_argument(
        "--output",
        type=Path,
        default=Path("test-results/chat-evals") / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
    )
    judge = actions.add_parser(
        "judge", help="Judge a saved judge-input.txt without rerunning Newsly"
    )
    judge.add_argument("input", type=Path)
    judge.add_argument("--model", required=True)
    judge.add_argument("--output", type=Path, required=True)


def execute(args: argparse.Namespace) -> int:
    if args.chat_action == "generate":
        scenario = Scenario.model_validate(read_yaml(args.scenario))
        sql = generate_sql(scenario, args.namespace)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(sql)
        print(args.output)
        return 0
    if args.chat_action == "judge":
        result = run_judge(args.input.read_text(), args.model)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result.model_dump(), indent=2) + "\n")
        print("pass" if result.passed else "fail")
        return int(not result.passed)
    if args.timeout <= 0 or args.max_turns <= 0:
        raise ValueError("timeout and max-turns must be positive")
    return run_suite(
        suite_path=args.suite,
        output=args.output,
        binary_dir=args.binary_dir,
        postgres_url=args.postgres_url,
        env_file=args.env_file,
        model=args.model,
        judge_model=args.judge_model,
        selected=args.cases,
        timeout=args.timeout,
        max_turns=args.max_turns,
        keep_database=args.keep_database,
    )
