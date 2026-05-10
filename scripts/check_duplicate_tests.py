"""Fail when legacy `app/tests` modules are reintroduced after migration."""

from __future__ import annotations

from pathlib import Path


def collect_relative_modules(root: Path) -> set[str]:
    """Collect python module paths relative to a root directory."""
    if not root.exists():
        return set()
    return {str(path.relative_to(root)) for path in root.rglob("test_*.py")}


def main() -> int:
    """CLI entrypoint."""
    legacy_root = Path("app/tests")
    legacy_modules = sorted(collect_relative_modules(legacy_root))
    if not legacy_modules:
        print("Legacy app/tests root is clean.")
        return 0

    print("Legacy app/tests modules detected after migration:")
    for module in legacy_modules:
        print(f" - {module}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
