"""Compare Python embedding pipelines using the production Rust relation policy."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, cast

from newsly_evals.encoders import (
    OpenAICompatibleEncoder,
    SentenceTransformerEncoder,
)
from newsly_evals.news_relation_cases import (
    NEGATIVE_PRODUCTION_CLUSTER_CASES,
    PRODUCTION_CLUSTER_CASES,
)
from newsly_evals.relations import run_relation_eval

DEFAULT_LOCAL_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_OPENROUTER_MODEL = "qwen/qwen3-embedding-8b"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare embedding models with canonical Rust relation scoring"
    )
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--threshold", action="append", dest="thresholds")
    parser.add_argument("--local-model", default=DEFAULT_LOCAL_MODEL)
    parser.add_argument("--local-label", default="local")
    parser.add_argument("--skip-local", action="store_true")
    parser.add_argument("--openrouter-model", default=DEFAULT_OPENROUTER_MODEL)
    parser.add_argument("--openrouter-label", default="openrouter-qwen3-embedding-8b")
    parser.add_argument("--openrouter-base-url", default=DEFAULT_OPENROUTER_BASE_URL)
    parser.add_argument("--openrouter-api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--openrouter-batch-size", type=int, default=32)
    parser.add_argument("--openrouter-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--openrouter-provider-order", action="append")
    parser.add_argument("--openrouter-provider-sort")
    parser.add_argument("--allow-provider-data-collection", action="store_true")
    parser.add_argument("--skip-openrouter", action="store_true")
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--failures-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-failed-cases", action="store_true")
    return parser.parse_args()


def _cases(case_ids: set[str] | None) -> list[dict[str, Any]]:
    cases = cast(
        list[dict[str, Any]],
        [*PRODUCTION_CLUSTER_CASES, *NEGATIVE_PRODUCTION_CLUSTER_CASES],
    )
    if not case_ids:
        return cases
    return [case for case in cases if str(case["case_id"]) in case_ids]


def _thresholds(specs: list[str] | None) -> list[dict[str, Any]]:
    if not specs:
        return [{"label": "current", "primary": 0.85, "secondary": 0.75}]
    parsed = []
    for spec in specs:
        parts = spec.split(":")
        if len(parts) != 3:
            raise ValueError(f"invalid threshold {spec!r}; expected label:primary:secondary")
        label, primary, secondary = parts
        parsed.append(
            {"label": label.strip(), "primary": float(primary), "secondary": float(secondary)}
        )
    return parsed


def _openrouter_extra_body(args: argparse.Namespace) -> dict[str, Any]:
    provider: dict[str, Any] = {
        "allow_fallbacks": False,
        "require_parameters": True,
        "zdr": True,
    }
    if not args.allow_provider_data_collection:
        provider["data_collection"] = "deny"
    if args.openrouter_provider_order:
        provider["order"] = args.openrouter_provider_order
    if args.openrouter_provider_sort:
        provider["sort"] = args.openrouter_provider_sort
    return {"provider": provider}


def _variants(args: argparse.Namespace) -> list[tuple[str, str, Any, dict[str, Any]]]:
    variants: list[tuple[str, str, Any, dict[str, Any]]] = []
    if not args.skip_local:
        variants.append(
            (
                args.local_label,
                "local",
                SentenceTransformerEncoder(args.local_model),
                {"provider": "sentence_transformers"},
            )
        )
    if not args.skip_openrouter:
        api_key = os.environ.get(args.openrouter_api_key_env)
        if not api_key:
            raise ValueError(f"{args.openrouter_api_key_env} is not configured")
        variants.append(
            (
                args.openrouter_label,
                "openrouter",
                OpenAICompatibleEncoder(
                    model=args.openrouter_model,
                    api_key=api_key,
                    base_url=args.openrouter_base_url,
                    batch_size=args.openrouter_batch_size,
                    timeout_seconds=args.openrouter_timeout_seconds,
                    extra_body=_openrouter_extra_body(args),
                ),
                {
                    "provider": "openrouter",
                    "routing": _openrouter_extra_body(args)["provider"],
                },
            )
        )
    if not variants:
        raise ValueError("at least one embedding variant must be selected")
    return variants


def _print_text(runs: list[dict[str, Any]], *, failures_only: bool) -> None:
    for run in runs:
        variant = run["variant"]
        for scored in run["result"]["runs"]:
            summary = scored["summary"]
            threshold = scored["threshold"]
            timing = run["result"]["embedding_bundle_metadata"]["timings_ms"]
            print(
                f"[{variant['label']} | {threshold['label']}] "
                f"{summary['passed_count']}/{summary['case_count']} passed "
                f"macro_f1={summary['macro_f1']:.3f} "
                f"precision={summary['macro_precision']:.3f} "
                f"recall={summary['macro_recall']:.3f} "
                f"encode={timing['encoding']:.0f}ms"
            )
            for result in scored["results"]:
                if failures_only and result["passed"]:
                    continue
                status = "PASS" if result["passed"] else "FAIL"
                print(
                    f"{status} {result['case_id']} f1={result['f1']:.3f} "
                    f"precision={result['precision']:.3f} recall={result['recall']:.3f} "
                    f"{result['label']}"
                )
        print()


def main() -> int:
    args = _parse_args()
    cases = _cases(set(args.case_ids or []) or None)
    thresholds = _thresholds(args.thresholds)
    runs = []
    for label, backend, encoder, metadata in _variants(args):
        runs.append(
            {
                "variant": {"label": label, "backend": backend, "model": encoder.model},
                "result": run_relation_eval(
                    raw_cases=cases,
                    encoder=encoder,
                    thresholds=thresholds,
                    include_traces=args.trace,
                    provider_metadata=metadata,
                ),
            }
        )
    payload = {"case_count": len(cases), "runs": runs}
    encoded = json.dumps(payload, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    if args.json and not args.output:
        print(encoded)
    elif not args.json:
        _print_text(runs, failures_only=args.failures_only)
    failed = any(
        scored["summary"]["failed_count"] for run in runs for scored in run["result"]["runs"]
    )
    return int(args.fail_on_failed_cases and failed)


if __name__ == "__main__":
    raise SystemExit(main())
