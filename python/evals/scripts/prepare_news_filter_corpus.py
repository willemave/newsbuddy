"""Prepare a frozen filtering corpus from existing public Rust operator exports."""

import argparse
import json
from pathlib import Path

from newsly_evals.news_filter_corpus import DEFAULT_SEED, prepare_corpus


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-export-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    args = parser.parse_args()
    manifest = prepare_corpus(args.operator_export_dir, args.output_dir, args.seed)
    print(
        json.dumps(
            {
                key: manifest[key]
                for key in ("sample_count", "sample_sha256", "aggregator_counts", "split_counts")
            }
        )
    )


if __name__ == "__main__":
    main()
