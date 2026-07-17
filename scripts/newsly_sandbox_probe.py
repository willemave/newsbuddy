#!/usr/bin/env python3
"""Report the required Newsly agent-sandbox capabilities as JSON."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess


def _version(command: str) -> str | None:
    path = shutil.which(command)
    if path is None:
        return None
    result = subprocess.run(
        [path, "--version"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return (result.stdout or result.stderr).strip().splitlines()[0] or path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.parse_args()
    chromium = next(
        (
            value
            for command in ("chromium", "chromium-browser", "google-chrome")
            if (value := _version(command)) is not None
        ),
        None,
    )
    payload = {
        "bash": _version("bash"),
        "python": _version("python3"),
        "node": _version("node"),
        "git": _version("git"),
        "curl": _version("curl"),
        "jq": _version("jq"),
        "chromium": chromium,
        "playwright": bool(importlib.util.find_spec("playwright")),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if all(payload.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
