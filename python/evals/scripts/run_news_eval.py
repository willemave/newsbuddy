"""Run frozen feed-style relation evals through the production Rust policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from newsly_evals.artifacts import read_jsonl_records
from newsly_evals.encoders import SentenceTransformerEncoder
from newsly_evals.relations import (
    build_feed_relation_cases,
    run_document_relation_eval,
)

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_SLICES = (
    "mixed_source_windows",
    "user_scoped_x_windows",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen feed windows with Python embeddings and Rust relation policy"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("python/evals/datasets/news_shortform"),
        help="Directory containing frozen JSONL slices",
    )
    parser.add_argument("--slice", action="append", dest="slices")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--primary-threshold",
        type=float,
    )
    parser.add_argument(
        "--secondary-threshold",
        type=float,
    )
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-failed-cases", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    requested_slices = args.slices or list(DEFAULT_SLICES)
    if (args.primary_threshold is None) != (args.secondary_threshold is None):
        raise ValueError("primary and secondary thresholds must be overridden together")
    thresholds = (
        [
            {
                "label": "override",
                "primary": args.primary_threshold,
                "secondary": args.secondary_threshold,
            }
        ]
        if args.primary_threshold is not None
        else []
    )
    records_by_slice = {
        slice_name: read_jsonl_records(args.input_dir / f"{slice_name}.jsonl")
        for slice_name in requested_slices
    }
    for slice_name, records in records_by_slice.items():
        if not records:
            raise ValueError(f"configured relation slice {slice_name!r} contains no records")
    encoder = SentenceTransformerEncoder(args.model)
    slice_results: dict[str, dict[str, Any]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    failed_count = 0

    for slice_name in requested_slices:
        records = records_by_slice[slice_name]
        cases = build_feed_relation_cases(records, label_prefix=slice_name)
        if not cases:
            raise ValueError(f"configured relation slice {slice_name!r} produced no cases")
        result = run_document_relation_eval(
            cases=cases,
            encoder=encoder,
            thresholds=thresholds,
            include_traces=args.trace,
            provider_metadata={
                "pipeline": "frozen_feed_sentence_transformers",
                "slice": slice_name,
            },
        )
        slice_results[slice_name] = result
        summary = result["runs"][0]["summary"]
        summaries[slice_name] = summary
        failed_count += int(summary["failed_count"])

    payload = {
        "schema_version": 1,
        "artifact_type": "newsly.feed_relation_eval.result",
        "config": {
            "model": args.model,
            "threshold": next(iter(slice_results.values()))["runs"][0]["threshold"],
            "slices": requested_slices,
            "policy_owner": "newsly-eval-driver",
        },
        "summaries": summaries,
        "slice_results": slice_results,
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return int(args.fail_on_failed_cases and failed_count > 0)


if __name__ == "__main__":
    raise SystemExit(main())
