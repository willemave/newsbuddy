#!/usr/bin/env python3
"""Fail when key high-churn modules grow past agreed line-count guardrails."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MAX_LINES = 1_000
SOURCE_GLOBS = ("app/**/*.py", "client/newsly/newsly/**/*.swift")
IGNORED_PREFIXES = ("client/newsly/newsly/Models/Generated/",)


def load_guardrails(config_path: Path) -> dict[str, int]:
    """Load guardrail line limits from JSON config."""
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Guardrail config must be a JSON object mapping path -> line limit")

    parsed: dict[str, int] = {}
    for path, limit in data.items():
        if not isinstance(path, str):
            raise ValueError(f"Guardrail key must be a string path: {path!r}")
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError(f"Guardrail limit for {path!r} must be a positive integer")
        parsed[path] = limit
    return parsed


def count_lines(path: Path) -> int:
    """Return total line count for a file."""
    return sum(1 for _ in path.open("r", encoding="utf-8"))


def discover_guardrails(
    repo_root: Path,
    explicit_guardrails: dict[str, int],
) -> dict[str, int]:
    """Apply a default ceiling to every non-generated Python and Swift module."""
    guardrails = dict(explicit_guardrails)
    for source_glob in SOURCE_GLOBS:
        for file_path in repo_root.glob(source_glob):
            rel_path = file_path.relative_to(repo_root).as_posix()
            if rel_path.startswith(IGNORED_PREFIXES):
                continue
            guardrails.setdefault(rel_path, DEFAULT_MAX_LINES)
    return guardrails


def main() -> int:
    """Run the guardrail check."""
    repo_root = Path(__file__).resolve().parent.parent
    config_path = repo_root / "config/module_size_guardrails.json"
    explicit_guardrails = load_guardrails(config_path)
    guardrails = discover_guardrails(repo_root, explicit_guardrails)

    violations: list[tuple[str, int, int]] = []
    missing: list[str] = []

    for rel_path, limit in guardrails.items():
        file_path = repo_root / rel_path
        if not file_path.exists():
            missing.append(rel_path)
            continue
        line_count = count_lines(file_path)
        if line_count > limit:
            violations.append((rel_path, line_count, limit))

    if missing:
        print("Missing guardrail targets:")
        for rel_path in missing:
            print(f"- {rel_path}")

    if violations:
        print("Module size guardrail violations:")
        for rel_path, line_count, limit in violations:
            print(f"- {rel_path}: {line_count} lines (limit {limit})")

    if missing or violations:
        return 1

    print(
        "Module size guardrails OK "
        f"({len(guardrails)} files checked, {len(explicit_guardrails)} ratcheted)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
