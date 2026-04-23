"""Shared fixtures for aggregator scraper tests."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "aggregators"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


def load_fixture(*parts: str) -> str:
    return (FIXTURES_DIR.joinpath(*parts)).read_text(encoding="utf-8")
