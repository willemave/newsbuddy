"""Generate Go CLI contracts from backend canonical models and enums."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.contracts_codegen.go_emitter import build_go_contracts  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse script arguments."""
    parser = argparse.ArgumentParser(description="Generate Go CLI API contracts")
    parser.add_argument(
        "--output",
        default="cli/internal/api/contracts_gen.go",
        help="Output Go contract file path",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint."""
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_go_contracts(), encoding="utf-8")
    print(f"Generated Go CLI contracts: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
