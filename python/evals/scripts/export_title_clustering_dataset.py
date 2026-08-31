"""Normalize a Rust-exported read-only snapshot into offline eval artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from newsly_evals.artifacts import (
    TITLE_CLUSTERING_MANIFEST_ARTIFACT,
    TITLE_CLUSTERING_ROW_ARTIFACT,
    file_sha256,
    read_jsonl_records,
    write_json_artifact,
    write_jsonl_artifact,
)
from newsly_evals.title_clustering import (
    build_duplicate_summary,
    normalize_dataset_record,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize a read-only JSONL snapshot exported by Rust into versioned "
            "title-clustering dataset artifacts. This command never opens Newsly's database."
        )
    )
    parser.add_argument(
        "--input-jsonl",
        type=Path,
        required=True,
        help="Read-only JSONL snapshot produced by Rust/operator tooling",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10_000,
        help="Maximum number of source rows to retain",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("outputs/title_clustering"),
        help="Directory for generated offline artifacts",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.limit < 1:
        raise ValueError("--limit must be positive")
    if not args.input_jsonl.is_file():
        raise FileNotFoundError(f"input JSONL not found: {args.input_jsonl}")

    source_rows = read_jsonl_records(args.input_jsonl)[: args.limit]
    records = [normalize_dataset_record(row) for row in source_rows]
    for index, record in enumerate(records, start=1):
        content_id = record.get("content_id")
        if not isinstance(content_id, int) or isinstance(content_id, bool):
            raise ValueError(f"source row {index} has no integer content_id")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = args.out_dir / f"content_rows_last_{args.limit}.jsonl"
    duplicates_path = args.out_dir / f"content_title_duplicates_last_{args.limit}.json"
    manifest_path = args.out_dir / f"content_rows_last_{args.limit}.manifest.json"

    dataset_sha256 = write_jsonl_artifact(
        dataset_path,
        records,
        artifact_type=TITLE_CLUSTERING_ROW_ARTIFACT,
    )
    duplicate_payload = build_duplicate_summary(records)
    write_json_artifact(
        duplicates_path,
        duplicate_payload,
        artifact_type="newsly.title_clustering.duplicate_summary",
    )
    write_json_artifact(
        manifest_path,
        {
            "source_path": str(args.input_jsonl),
            "source_sha256": file_sha256(args.input_jsonl),
            "requested_limit": args.limit,
            "dataset_path": str(dataset_path),
            "dataset_sha256": dataset_sha256,
            "duplicates_path": str(duplicates_path),
            "record_count": len(records),
            "database_access": False,
        },
        artifact_type=TITLE_CLUSTERING_MANIFEST_ARTIFACT,
    )
    print(
        json.dumps(
            {
                "dataset_path": str(dataset_path),
                "duplicates_path": str(duplicates_path),
                "manifest_path": str(manifest_path),
                "record_count": len(records),
                "dataset_sha256": dataset_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
