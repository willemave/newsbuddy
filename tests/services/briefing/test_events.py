from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import ContentClassification, ContentType
from app.models.db import BriefingPendingSource
from app.services.briefing.events import enqueue_content_for_briefing_if_ready


def test_enqueue_content_for_briefing_accepts_null_classification(
    db_session: Session,
    test_user,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [])
    test_user.reading_experience = "briefing"
    db_session.flush()
    content = content_factory(
        content_type=ContentType.PODCAST,
        title="Unclassified episode",
        classification=None,
    )
    status_entry_factory(user=test_user, content=content, status="inbox")

    enqueued = enqueue_content_for_briefing_if_ready(
        db_session,
        content_id=content.id,
        settings=settings,
    )

    assert enqueued == 1
    db_session.flush()
    pending = db_session.query(BriefingPendingSource).one()
    assert pending.source_kind == "content"
    assert pending.source_id == content.id
    assert pending.lens_key == "podcasts"


def test_enqueue_content_for_briefing_excludes_skip_classification(
    db_session: Session,
    test_user,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [test_user.id])
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title="Skipped article",
        classification=ContentClassification.SKIP.value,
    )
    status_entry_factory(user=test_user, content=content, status="inbox")

    enqueued = enqueue_content_for_briefing_if_ready(
        db_session,
        content_id=content.id,
        settings=settings,
    )

    assert enqueued == 0
    assert db_session.query(BriefingPendingSource).count() == 0
