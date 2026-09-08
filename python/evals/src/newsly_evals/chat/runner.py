"""Local scenario → HTTP conversation → judge → report orchestration."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from newsly_evals.artifacts import file_sha256
from newsly_evals.chat.client import CandidateTurnError, ChatClient
from newsly_evals.chat.generator import generate_sql
from newsly_evals.chat.judge import (
    JUDGE_VERSION,
    exact_checks,
    judge_prompt,
    outcome,
    response_checks,
    run_judge,
)
from newsly_evals.chat.runtime import CHAT_RUNTIME_LIMITS, LocalRuntime, load_environment
from newsly_evals.chat.schema import Case, Scenario, load_suite
from newsly_evals.chat.stubs import StubServer
from newsly_evals.chat.trace import tool_evidence


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def report(output: Path, results: list[dict[str, Any]]) -> None:
    lines = ["# Chat eval results", ""]
    for result in results:
        lines += [
            f"## {result['id']} — {result['status']}",
            "",
            "Expected: " + result["expects"],
            "",
        ]
        for turn in result.get("turns", []):
            trace = tool_evidence(turn)
            tools = ", ".join(f"{name} ×{count}" for name, count in trace["counts"].items())
            lines += [
                "Query: " + turn["query"],
                "",
                "Tools reported by API (diagnostic only): " + (tools or "none observed"),
                "",
                "Tool evidence: "
                + ("complete summary" if trace["complete"] else "unavailable/incomplete")
                + "; names/counts only, no raw results or call order.",
                "",
                "### Final answer",
                "",
                turn["answer"],
                "",
            ]
            options = turn.get("assistant", {}).get("feed_options", [])
            if options:
                lines += ["### Subscription cards", ""]
                lines += [
                    f"- {option.get('title', 'Untitled')}: {option.get('feed_url', '')}"
                    for option in options
                ]
                lines += [""]
        lines += ["### Evaluation", ""]
        for check in result.get("checks", []):
            if not check["passed"]:
                lines += [f"- Failed {check['name']}: {check['detail']}"]
        for criterion, value in result.get("judgment", {}).items():
            lines += [f"- {criterion}: {'pass' if value['passed'] else 'fail'} — {value['reason']}"]
        if "error" in result:
            lines += ["", "Error: " + result["error"]]
        lines += [""]
    (output / "report.md").write_text("\n".join(lines))
    save_json(output / "results.json", {"version": 1, "results": results})


def run_case(
    case: Case,
    scenario: Scenario,
    runtime: LocalRuntime,
    output: Path,
    model: str,
    judge_model: str,
    timeout: float,
) -> dict[str, Any]:
    output.mkdir()
    namespace = "c-" + output.name + "-" + runtime.name.removeprefix("newsly_eval_")
    # Case names can be long; namespaces are bounded independently of fixture logical keys.
    namespace = "c-" + hashlib.sha256(namespace.encode()).hexdigest()[:24]
    result: dict[str, Any] = {
        "id": case.id,
        "expects": case.expects,
        "status": "error",
        "turns": [],
        "namespace": namespace,
        "checks": [],
    }
    started = time.monotonic()
    client = None
    phase = "bootstrap"
    try:
        sql = generate_sql(scenario, namespace)
        (output / "seed.sql").write_text(sql)
        refs = runtime.seed(sql)
        save_json(output / "manifest.json", refs)
        runtime.stubs.reset(scenario.stubs)
        client = ChatClient(runtime.api_url, refs["user:" + case.user], timeout=timeout)
        session_id = refs["session:" + case.user]
        client.set_model(session_id, model)
        visible = {
            key: refs["episode:" + key]
            for key, item in scenario.episodes.items()
            if case.user in item.visible_to
        }
        before = client.content_state(visible)
        subscriptions_before = client.subscription_state()
        context = (
            {"screen_type": "content_detail", "content_id": refs["episode:" + case.context_episode]}
            if case.context_episode
            else {"screen_type": "unknown"}
        )
        phase = "candidate"
        for query in case.turns:
            turn = client.turn(session_id, query, context, runtime.check_processes)
            result["turns"].append(turn)
            save_json(output / "result.json", result)
        after = client.content_state(visible)
        subscriptions_after = client.subscription_state()
        checks = exact_checks(case, scenario, namespace, result["turns"][-1]["answer"])
        checks += response_checks(case, result["turns"])
        result["observations"] = {
            "subscriptions_changed": subscriptions_before != subscriptions_after,
            "read_saved_state_changed": before != after,
            "unmatched_external_calls": [
                item for item in runtime.stubs.requests if not item["matched"]
            ],
        }
        result["checks"] = [check.model_dump() for check in checks]
        result["state_before"], result["state_after"] = before, after
        result["subscriptions_before"] = subscriptions_before
        result["subscriptions_after"] = subscriptions_after
        prompt = judge_prompt(case, scenario, namespace, result["turns"])
        (output / "judge-input.txt").write_text(prompt)
        phase = "judge"
        judgment = run_judge(prompt, judge_model, timeout=timeout)
        result["judgment"] = judgment.model_dump()
        result["status"] = outcome(checks, judgment)
    except (RuntimeError, ValueError, OSError, TimeoutError, subprocess.SubprocessError) as error:
        result["error"] = f"{phase}: {error}"
        if isinstance(error, CandidateTurnError):
            result["failure_status"] = error.status
            save_json(output / "diagnostics.json", error.status)
    finally:
        if client:
            client.close()
        result["seconds"] = round(time.monotonic() - started, 3)
        result["stub_requests"] = list(runtime.stubs.requests)
        save_json(output / "result.json", result)
    return result


def run_suite(
    *,
    suite_path: Path,
    output: Path,
    binary_dir: Path,
    postgres_url: str,
    env_file: Path | None,
    model: str,
    judge_model: str,
    selected: list[str],
    timeout: float,
    max_turns: int,
    keep_database: bool = False,
) -> int:
    suite, scenario = load_suite(suite_path)
    unknown = set(selected) - {case.id for case in suite.cases}
    if unknown:
        raise ValueError(f"unknown case IDs: {sorted(unknown)}")
    cases = [case for case in suite.cases if not selected or case.id in selected]
    turns = sum(len(case.turns) for case in cases)
    if turns > max_turns:
        raise ValueError(f"suite needs {turns} turns, exceeding --max-turns={max_turns}")
    provider = model.partition(":")[0]
    hosts = {
        "openai": {"api.openai.com"},
        "anthropic": {"api.anthropic.com"},
        "openrouter": {"openrouter.ai"},
    }.get(provider)
    if not hosts:
        raise ValueError("candidate model needs openai:, anthropic:, or openrouter: prefix")
    environment = load_environment(env_file)
    if not environment.get(provider.upper() + "_API_KEY"):
        raise ValueError(f"{provider.upper()}_API_KEY is required for the selected candidate")
    output.mkdir(parents=True, exist_ok=False)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    manifest = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "revision": revision,
        "dirty_checkout": bool(
            subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True
            ).stdout.strip()
        ),
        "binary_sha256": {
            name: file_sha256(binary_dir / name)
            for name in ("newsly-api", "newsly-db", "newsly-worker")
        },
        "candidate_model": model,
        "judge_model": judge_model,
        "judge_version": JUDGE_VERSION,
        "suite_sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
        "scenario_sha256": hashlib.sha256(scenario.model_dump_json().encode()).hexdigest(),
        "candidate_turns": turns,
        "max_candidate_requests_per_turn": int(
            CHAT_RUNTIME_LIMITS["LLM_TASK_SANDBOX_REQUEST_LIMIT"]
        ),
        "max_candidate_tool_calls_per_turn": int(
            CHAT_RUNTIME_LIMITS["LLM_TASK_SANDBOX_TOOL_CALL_LIMIT"]
        ),
        "max_candidate_output_tokens_per_request": int(
            CHAT_RUNTIME_LIMITS["CHAT_OUTPUT_TOKEN_LIMIT"]
        ),
        "candidate_runtime_limits": CHAT_RUNTIME_LIMITS,
        "judge_requests": len(cases),
        "usage": None,
        "cost": None,
    }
    save_json(output / "run.json", manifest)
    results: list[dict[str, Any]] = []
    print(
        f"Running {len(cases)} cases: {turns} candidate turns via {model}; "
        f"{len(cases)} CLI judgments via {judge_model}. "
        f"Candidate limit: {manifest['max_candidate_requests_per_turn']} requests/turn, "
        f"{manifest['max_candidate_output_tokens_per_request']} output tokens/request.",
        flush=True,
    )
    try:
        with (
            StubServer(hosts) as stubs,
            LocalRuntime(
                postgres_url=postgres_url,
                binary_dir=binary_dir,
                output=output / "runtime",
                environment=environment,
                stubs=stubs,
                keep=keep_database,
            ) as runtime,
        ):
            manifest["database"] = runtime.name
            manifest["api_url"] = runtime.api_url
            save_json(output / "run.json", manifest)
            for case in cases:
                print(f"  {case.id}: running…", flush=True)
                result = run_case(
                    case, scenario, runtime, output / case.id, model, judge_model, timeout
                )
                results.append(result)
                report(output, results)
                print(f"  {case.id}: {result['status']}", flush=True)
                for check in result.get("checks", []):
                    if not check["passed"]:
                        print(f"    {check['name']}: {check['detail']}", flush=True)
                for criterion, verdict in result.get("judgment", {}).items():
                    if not verdict["passed"]:
                        print(f"    {criterion}: {verdict['reason']}", flush=True)
                if result.get("error"):
                    print(f"    {result['error']}", flush=True)
                # A timed-out/failed turn may still be running. Never reset stubs beneath it.
                if result["status"] == "error" and not result.get("error", "").startswith("judge:"):
                    break
    except (RuntimeError, ValueError, OSError, TimeoutError, subprocess.SubprocessError) as error:
        manifest["error"] = str(error)
        save_json(output / "run.json", manifest)
        report(output, results)
        print(f"Run error: {error}", flush=True)
        return 2
    print(f"Report: {output / 'report.md'}", flush=True)
    if any(result["status"] == "error" for result in results) or len(results) != len(cases):
        return 2
    return int(any(result["status"] == "fail" for result in results))
