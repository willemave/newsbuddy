"""Prepare versioned replay files from an already-exported operator artifact."""

import argparse
import hashlib
import json
from pathlib import Path

from newsly_evals.lens_replay import make_request, normalize_rows, operator_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-export", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cohort", choices=("all", "technology", "general"), default="all")
    parser.add_argument("--cadence", choices=("weekly", "nightly"), default="weekly")
    parser.add_argument("--compact-matrix", action="store_true")
    args = parser.parse_args()
    records, manifest = normalize_rows(operator_rows(args.operator_export))
    manifest["source_sha256"] = hashlib.sha256(args.operator_export.read_bytes()).hexdigest()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    metadata = [{k: v for k, v in row.items() if k != "vector"} for row in records]
    (args.output_dir / "story-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    request = make_request(records, args.cohort, args.cadence, wide=not args.compact_matrix)
    name = f"{args.cohort}-{args.cadence}"
    path = args.output_dir / f"{name}.input.json"
    path.write_text(json.dumps(request, separators=(",", ":")) + "\n")
    print(
        json.dumps(
            {
                "request": str(path),
                "rows": len(request["items"]),
                "checkpoints": len(request["checkpoints"]),
                "variants": len(request["variants"]),
                "excluded": manifest["excluded"],
            }
        )
    )


if __name__ == "__main__":
    main()
