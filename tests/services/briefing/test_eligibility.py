from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.services.briefing.eligibility import briefing_enabled_user_ids


def test_new_profile_defaults_to_briefing(user_factory) -> None:
    user = user_factory()

    assert user.reading_experience == "briefing"


def test_active_classic_profile_is_briefing_eligible(
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [])
    test_user.reading_experience = "classic"
    db_session.flush()

    enabled = briefing_enabled_user_ids(
        db_session,
        candidate_user_ids=[test_user.id],
        settings=settings,
    )

    assert enabled == {test_user.id}


def test_inactive_profile_is_not_automatically_briefing_eligible(
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [])
    test_user.is_active = False
    db_session.flush()

    enabled = briefing_enabled_user_ids(
        db_session,
        candidate_user_ids=[test_user.id],
        settings=settings,
    )

    assert enabled == set()
