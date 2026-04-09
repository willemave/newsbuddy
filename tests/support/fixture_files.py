"""Shared fixture file helpers for Python and iOS test harnesses."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def load_json_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture from ``tests/fixtures``."""
    fixture_path = FIXTURES_DIR / f"{name}.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def encode_launch_fixture(name: str) -> str:
    """Encode a fixture payload for iOS launch arguments."""
    payload = json.dumps(load_json_fixture(name), separators=(",", ":")).encode("utf-8")
    return base64.b64encode(payload).decode("utf-8")
