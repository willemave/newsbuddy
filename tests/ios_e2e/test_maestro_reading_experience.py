"""Maestro coverage for the retained Classic fallback shell."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.ios_e2e]


def test_classic_lists_remain_available_as_fallback(
    run_ios_flow,
    test_user,
) -> None:
    """An explicit fallback mode should still expose both legacy feed roots."""
    assert test_user.reading_experience == "classic"

    run_ios_flow("reading_experience_fallback.yaml")
