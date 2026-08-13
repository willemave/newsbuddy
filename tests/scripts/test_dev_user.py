from __future__ import annotations

from sqlalchemy.orm import Session

import scripts.generate_test_data as generate_test_data
import scripts.support.dev_user as dev_user


def _disable_fixture_images(monkeypatch) -> None:
    monkeypatch.setattr(
        generate_test_data,
        "_write_placeholder_images",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        dev_user,
        "_backfill_briefing_segment_images",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(dev_user, "_remove_generated_images", lambda _content_ids: None)


def test_showcase_profile_is_idempotent(
    db_session: Session, monkeypatch, stub_briefing_layout_generator
) -> None:
    _disable_fixture_images(monkeypatch)

    first = dev_user.setup_showcase_user(db_session, briefing_mode="deterministic")
    second = dev_user.setup_showcase_user(db_session, briefing_mode="deterministic")

    assert second["user"]["id"] == first["user"]["id"]
    assert second["user"]["email"] == "debug+showcase@example.com"
    assert second["content"] == {
        "articles": 8,
        "podcasts": 6,
        "news": 24,
        "read": 3,
        "saved": 3,
    }
    assert second["briefing"]["lenses"]
    assert second["briefing"]["counts"]["segments"] > 0


def test_onboarding_profile_reuses_showcase_identity(db_session: Session, monkeypatch) -> None:
    _disable_fixture_images(monkeypatch)
    showcase = dev_user.setup_showcase_user(db_session, briefing_mode="none")

    onboarding = dev_user.setup_onboarding_user(db_session, state="ready")

    assert onboarding["user"]["id"] == showcase["user"]["id"]
    assert onboarding["profile"] == "onboarding"
    assert onboarding["content"] == {
        "articles": 0,
        "podcasts": 0,
        "news": 0,
        "read": 0,
        "saved": 0,
    }
    assert onboarding["onboarding"]["state"] == "ready"
    assert onboarding["onboarding"]["completed_sources"] == 4
    assert [lens["key"] for lens in onboarding["briefing"]["lenses"]] == [
        dev_user.ONBOARDING_LENS_KEY
    ]
    user = dev_user.find_showcase_user(db_session)
    assert user is not None
    assert dev_user.dev_user_status(db_session, user=user)["profile"] == "onboarding"
