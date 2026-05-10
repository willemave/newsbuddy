from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content, ContentStatusEntry
from app.services.scraper_configs import ensure_inbox_status


def test_ensure_inbox_status_allows_news(db_session, test_user) -> None:
    content = Content(
        url="https://example.com/news-item",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={"source": "Example"},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)
    assert test_user.id is not None
    assert content.id is not None

    created = ensure_inbox_status(
        db_session,
        user_id=test_user.id,
        content_id=content.id,
        content_type=content.content_type,
    )
    if created:
        db_session.commit()

    status_row = (
        db_session.query(ContentStatusEntry)
        .filter(
            ContentStatusEntry.user_id == test_user.id,
            ContentStatusEntry.content_id == content.id,
        )
        .first()
    )
    assert status_row is not None
