"""Tests for instruction link content creation."""

import pytest

from app.constants import SELF_SUBMISSION_SOURCE
from app.models.internal.content_analyzer import InstructionLink
from app.models.schema import Content, ContentStatusEntry
from app.services.instruction_links import create_contents_from_instruction_links


@pytest.fixture
def source_content(db_session, test_user):
    content = Content(
        url="https://example.com/original",
        content_type="unknown",
        title=None,
        source=SELF_SUBMISSION_SOURCE,
        platform=None,
        is_aggregate=False,
        status="new",
        classification="to_read",
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": test_user.id,
            "submitted_via": "share_sheet",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)
    return content


def test_create_contents_from_instruction_links_creates_new(db_session, source_content):
    created = []

    def enqueue_stub(content_id: int) -> None:
        created.append(content_id)

    links = [InstructionLink(url="https://example.com/linked")]

    new_ids = create_contents_from_instruction_links(
        db_session,
        source_content,
        links,
        enqueue_task=enqueue_stub,
    )

    assert len(new_ids) == 1
    assert created == new_ids

    new_content = db_session.query(Content).filter(Content.id == new_ids[0]).first()
    assert new_content is not None
    assert new_content.url == "https://example.com/linked"
    assert new_content.content_type == "unknown"
    assert new_content.title is None
    assert new_content.content_metadata.get("submitted_via") == "share_sheet_instruction"

    status_entry = (
        db_session.query(ContentStatusEntry)
        .filter(
            ContentStatusEntry.user_id
            == source_content.content_metadata.get("submitted_by_user_id"),
            ContentStatusEntry.content_id == new_ids[0],
        )
        .first()
    )
    assert status_entry is not None


def test_create_contents_from_instruction_links_skips_existing(db_session, source_content):
    existing = Content(
        url="https://example.com/existing",
        content_type="unknown",
        title=None,
        source=SELF_SUBMISSION_SOURCE,
        platform=None,
        is_aggregate=False,
        status="new",
        classification="to_read",
        content_metadata={},
    )
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)

    created = []

    def enqueue_stub(content_id: int) -> None:
        created.append(content_id)

    links = [InstructionLink(url="https://example.com/existing")]

    new_ids = create_contents_from_instruction_links(
        db_session,
        source_content,
        links,
        enqueue_task=enqueue_stub,
    )

    assert new_ids == []
    assert created == []

    status_entry = (
        db_session.query(ContentStatusEntry)
        .filter(
            ContentStatusEntry.user_id
            == source_content.content_metadata.get("submitted_by_user_id"),
            ContentStatusEntry.content_id == existing.id,
        )
        .first()
    )
    assert status_entry is not None
