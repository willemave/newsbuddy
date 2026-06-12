"""Generate Swift contracts from backend canonical models and enums."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.contracts_codegen.swift_emitter import (  # noqa: E402
    build_swift_contracts,
    build_swift_models,
)


def parse_args() -> argparse.Namespace:
    """Parse script arguments."""
    parser = argparse.ArgumentParser(description="Generate iOS API contracts")
    parser.add_argument(
        "--output",
        default="client/newsly/newsly/Models/Generated/APIContracts.generated.swift",
        help="Output Swift enum file path",
    )
    parser.add_argument(
        "--models-output",
        default="client/newsly/newsly/Models/Generated/APIModels.generated.swift",
        help="Output Swift model file path",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint."""
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_swift_contracts(), encoding="utf-8")
    print(f"Generated Swift contracts: {output}")
    models_output = Path(args.models_output)
    models_output.parent.mkdir(parents=True, exist_ok=True)
    models_output.write_text(build_swift_models(), encoding="utf-8")
    print(f"Generated Swift models: {models_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
