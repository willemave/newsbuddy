from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.db import BriefingLens, BriefingPendingSource
from app.models.db.users import User
from app.services.briefing.lenses import assign_pending_lenses


def test_stale_low_volume_news_uses_misc_lens_without_embedding(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_new_lens_min_items", 3)
    assert test_user.id is not None
    user_id = test_user.id
    item = news_item_factory(
        raw_metadata={},
        visibility_scope="user",
        owner_user_id=user_id,
    )

    db_session.add(
        BriefingLens(
            user_id=user_id,
            key="news-ai",
            tier="news",
            title="AI",
            deck="Artificial intelligence reads.",
            position=2,
            status="active",
            centroid=[0.1, 0.2],
        )
    )
    pending = BriefingPendingSource(
        user_id=user_id,
        source_kind="news",
        source_id=item.id,
        enqueued_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2),
    )
    db_session.add(pending)
    db_session.flush()

    def fail_encode(_texts):  # noqa: ANN001
        raise AssertionError("stale single-item lens assignment should not embed")

    monkeypatch.setattr("app.services.briefing.lenses.encode_news_texts", fail_encode)

    changed = assign_pending_lenses(db_session, user_id=user_id, settings=settings)

    assert changed == 1
    assert pending.lens_key == "misc"
    misc_lens = (
        db_session.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id, BriefingLens.key == "misc")
        .one()
    )
    assert misc_lens.title == "Briefs"
