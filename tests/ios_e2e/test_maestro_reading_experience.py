"""Maestro coverage for changing the persisted iOS reading experience."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.ios_e2e]


def test_settings_switches_from_classic_to_briefing(
    run_ios_flow,
    db_session,
    test_user,
) -> None:
    """The real Settings control should persist and activate Briefing."""
    assert test_user.reading_experience == "classic"

    run_ios_flow("reading_experience_toggle.yaml")

    db_session.refresh(test_user)
    assert test_user.reading_experience == "briefing"
