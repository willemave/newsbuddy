"""Tests for terminal content-worker failure paths."""

from __future__ import annotations

from contextlib import contextmanager

from pydantic import HttpUrl, TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.contracts import ContentStatus, ContentType
from app.models.db import (
    Content,
    ContentKnowledgeSave,
    UserIntegrationConnection,
    UserIntegrationSyncedItem,
)
from app.models.domain.content_mapper import content_to_domain
from app.pipeline.worker import ContentWorker


def _require_id(value: int | None) -> int:
    assert value is not None
    return value


def _patch_worker_db(monkeypatch, db_session) -> None:
    @contextmanager
    def _get_db_override():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    monkeypatch.setattr("app.pipeline.worker.get_db", _get_db_override)


def _attach_x_bookmark(
    db_session: Session,
    content: Content,
    *,
    user_id: int = 1,
) -> UserIntegrationSyncedItem:
    connection = UserIntegrationConnection(
        user_id=user_id,
        provider="x",
        provider_user_id=f"worker-test-{content.id}",
        is_active=True,
    )
    db_session.add(connection)
    db_session.flush()
    synced_item = UserIntegrationSyncedItem(
        connection_id=connection.id,
        channel="bookmarks",
        external_item_id=f"bookmark-{content.id}",
        content_id=content.id,
    )
    db_session.add_all(
        [
            synced_item,
            ContentKnowledgeSave(user_id=user_id, content_id=content.id),
        ]
    )
    db_session.commit()
    return synced_item


def test_update_canonical_url_marks_existing_content_id(monkeypatch, db_session) -> None:
    _patch_worker_db(monkeypatch, db_session)

    existing = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/canonical",
        status=ContentStatus.NEW.value,
        content_metadata={},
    )
    incoming = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/original",
        status=ContentStatus.NEW.value,
        content_metadata={},
    )
    db_session.add_all([existing, incoming])
    db_session.commit()
    db_session.refresh(existing)
    db_session.refresh(incoming)
    existing_id = _require_id(existing.id)

    worker = ContentWorker()
    domain_content = content_to_domain(incoming)
    worker._update_canonical_url(domain_content, "https://example.com/canonical")

    assert domain_content.metadata["canonical_content_id"] == existing_id
    assert str(domain_content.url) == "https://example.com/original"


def test_handle_canonical_integrity_conflict_marks_content_skipped(monkeypatch, db_session) -> None:
    _patch_worker_db(monkeypatch, db_session)

    existing = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/dupe",
        status=ContentStatus.PROCESSING.value,
        content_metadata={},
    )
    incoming = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/unique",
        status=ContentStatus.PROCESSING.value,
        content_metadata={"submitted_by_user_id": 1, "submitted_via": "x_bookmarks"},
    )
    db_session.add_all([existing, incoming])
    db_session.commit()
    synced_item = _attach_x_bookmark(db_session, incoming)
    db_session.refresh(existing)
    db_session.refresh(incoming)
    existing_id = _require_id(existing.id)

    worker = ContentWorker()
    domain_content = content_to_domain(incoming)
    domain_content.url = TypeAdapter(HttpUrl).validate_python("https://example.com/dupe")
    integrity_error = IntegrityError(
        "UPDATE contents ...",
        {},
        Exception("UNIQUE constraint failed: contents.url, contents.content_type"),
    )

    handled = worker._handle_canonical_integrity_conflict(domain_content, integrity_error)
    assert handled is True

    db_session.refresh(incoming)
    db_session.refresh(synced_item)
    assert incoming.status == ContentStatus.SKIPPED.value
    assert incoming.content_metadata is not None
    assert incoming.content_metadata["canonical_content_id"] == existing_id
    assert synced_item.content_id == existing_id
    assert db_session.query(ContentKnowledgeSave).filter_by(user_id=1, content_id=existing_id).one()
    assert (
        db_session.query(ContentKnowledgeSave).filter_by(user_id=1, content_id=incoming.id).first()
        is None
    )


def test_process_content_reconciles_bookmark_after_canonicalization(
    monkeypatch,
    db_session,
) -> None:
    _patch_worker_db(monkeypatch, db_session)

    existing = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/process-canonical",
        status=ContentStatus.COMPLETED.value,
        content_metadata={},
    )
    incoming = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/process-original",
        status=ContentStatus.NEW.value,
        content_metadata={"submitted_by_user_id": 1, "submitted_via": "x_bookmarks"},
    )
    db_session.add_all([existing, incoming])
    db_session.commit()
    synced_item = _attach_x_bookmark(db_session, incoming)
    existing_id = _require_id(existing.id)
    incoming_id = _require_id(incoming.id)

    def _process_article(_worker, content):  # noqa: ANN001
        content.metadata["canonical_content_id"] = existing_id
        content.metadata["content_to_summarize"] = "Processed article text"
        content.status = ContentStatus.PROCESSING
        return True

    monkeypatch.setattr(ContentWorker, "_process_article", _process_article)

    handled = ContentWorker().process_content(incoming_id, "test-worker")

    assert handled is True
    db_session.refresh(synced_item)
    assert synced_item.content_id == existing_id
    assert db_session.query(ContentKnowledgeSave).filter_by(user_id=1, content_id=existing_id).one()
    assert (
        db_session.query(ContentKnowledgeSave).filter_by(user_id=1, content_id=incoming_id).first()
        is None
    )


def test_process_content_handles_integrity_error_from_worker(monkeypatch, db_session) -> None:
    _patch_worker_db(monkeypatch, db_session)

    existing = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/dupe-worker",
        status=ContentStatus.PROCESSING.value,
        content_metadata={},
    )
    incoming = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/original-worker",
        status=ContentStatus.PROCESSING.value,
        content_metadata={},
    )
    db_session.add_all([existing, incoming])
    db_session.commit()
    db_session.refresh(existing)
    db_session.refresh(incoming)
    existing_id = _require_id(existing.id)
    incoming_id = _require_id(incoming.id)

    def _raise_integrity(_self, content):  # noqa: ANN001
        content.url = "https://example.com/dupe-worker"
        raise IntegrityError(
            "UPDATE contents ...",
            {},
            Exception("UNIQUE constraint failed: contents.url, contents.content_type"),
        )

    monkeypatch.setattr(ContentWorker, "_process_article", _raise_integrity)

    worker = ContentWorker()
    handled = worker.process_content(incoming_id, "test-worker")

    assert handled is True
    db_session.refresh(incoming)
    assert incoming.status == ContentStatus.SKIPPED.value
    assert incoming.content_metadata is not None
    assert incoming.content_metadata["canonical_content_id"] == existing_id


def test_process_content_preserves_concurrent_discussion_preview(
    monkeypatch,
    db_session,
) -> None:
    _patch_worker_db(monkeypatch, db_session)

    incoming = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/discussion-preview",
        status=ContentStatus.NEW.value,
        content_metadata={"source": "example.com"},
    )
    db_session.add(incoming)
    db_session.commit()
    db_session.refresh(incoming)
    incoming_id = _require_id(incoming.id)

    def _process_with_concurrent_discussion_update(worker, content):  # noqa: ANN001
        external_session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=db_session.get_bind(),
        )
        external_session = external_session_factory()
        try:
            external_content = (
                external_session.query(Content).filter(Content.id == content.id).first()
            )
            assert external_content is not None
            latest_metadata = dict(external_content.content_metadata or {})
            latest_metadata["top_comment"] = {
                "author": "alice",
                "text": "Great write-up",
            }
            latest_metadata["comment_count"] = 12
            external_content.content_metadata = latest_metadata
            external_session.commit()
        finally:
            external_session.close()

        content.status = ContentStatus.PROCESSING
        content.metadata["content_to_summarize"] = "test payload"
        return True

    monkeypatch.setattr(
        ContentWorker,
        "_process_article",
        _process_with_concurrent_discussion_update,
    )

    worker = ContentWorker()
    handled = worker.process_content(incoming_id, "test-worker")

    assert handled is True
    db_session.refresh(incoming)
    assert incoming.content_metadata is not None
    assert incoming.content_metadata.get("top_comment") == {
        "author": "alice",
        "text": "Great write-up",
    }
    assert incoming.content_metadata.get("comment_count") == 12
