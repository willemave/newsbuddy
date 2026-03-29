"""Tests for concurrent-safe content metadata merging helpers."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content
from app.services.content_metadata_merge import (
    ContentMetadataMergeError,
    compute_metadata_patch,
    refresh_merge_content_metadata,
)


def test_compute_metadata_patch_detects_updates_and_removed_keys() -> None:
    updates, removed = compute_metadata_patch(
        {"a": 1, "b": 2},
        {"a": 1, "c": 3},
    )

    assert updates == {"c": 3}
    assert removed == {"b"}


def test_refresh_merge_content_metadata_preserves_concurrent_keys(db_session) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/merge-preserve",
        status=ContentStatus.PROCESSING.value,
        content_metadata={"a": 1},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    external_session = sessionmaker(bind=db_session.get_bind())()
    try:
        external_content = external_session.query(Content).filter(Content.id == content.id).first()
        assert external_content is not None
        metadata = dict(external_content.content_metadata or {})
        metadata["concurrent"] = "yes"
        external_content.content_metadata = metadata
        external_session.commit()
    finally:
        external_session.close()

    merged = refresh_merge_content_metadata(
        db_session,
        content.id,
        base_metadata={"a": 1},
        updated_metadata={"a": 2},
    )

    assert merged["a"] == 2
    assert merged["concurrent"] == "yes"


def test_refresh_merge_content_metadata_applies_patch_removals(db_session) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/merge-removals",
        status=ContentStatus.PROCESSING.value,
        content_metadata={"keep": "v1", "drop": "v1"},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    external_session = sessionmaker(bind=db_session.get_bind())()
    try:
        external_content = external_session.query(Content).filter(Content.id == content.id).first()
        assert external_content is not None
        metadata = dict(external_content.content_metadata or {})
        metadata["extra"] = "concurrent"
        metadata["drop"] = "v2"
        external_content.content_metadata = metadata
        external_session.commit()
    finally:
        external_session.close()

    merged = refresh_merge_content_metadata(
        db_session,
        content.id,
        base_metadata={"keep": "v1", "drop": "v1"},
        updated_metadata={"keep": "v2"},
    )

    assert merged["keep"] == "v2"
    assert merged["extra"] == "concurrent"
    assert "drop" not in merged


def test_refresh_merge_content_metadata_can_preserve_latest_keys(db_session) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/merge-preserve-latest",
        status=ContentStatus.PROCESSING.value,
        content_metadata={"top_comment": {"author": "old", "text": "old"}, "x": 1},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    external_session = sessionmaker(bind=db_session.get_bind())()
    try:
        external_content = external_session.query(Content).filter(Content.id == content.id).first()
        assert external_content is not None
        metadata = dict(external_content.content_metadata or {})
        metadata["top_comment"] = {"author": "new", "text": "new"}
        external_content.content_metadata = metadata
        external_session.commit()
    finally:
        external_session.close()

    merged = refresh_merge_content_metadata(
        db_session,
        content.id,
        base_metadata={"top_comment": {"author": "old", "text": "old"}, "x": 1},
        updated_metadata={"top_comment": {"author": "old", "text": "old"}, "x": 2},
        preserve_latest_keys=("top_comment",),
    )

    assert merged["top_comment"] == {"author": "new", "text": "new"}
    assert merged["x"] == 2


def test_refresh_merge_content_metadata_raises_when_row_is_missing(db_session) -> None:
    with pytest.raises(ContentMetadataMergeError, match="Missing content metadata row"):
        refresh_merge_content_metadata(
            db_session,
            999_999,
            base_metadata={"a": 1},
            updated_metadata={"a": 2},
        )
