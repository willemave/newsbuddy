"""Tests for X bookmark Knowledge destination reconciliation."""

from app.models.db import (
    Content,
    ContentKnowledgeSave,
    UserIntegrationConnection,
    UserIntegrationSyncedItem,
)
from app.services.x_bookmark_destinations import (
    list_x_bookmark_destination_content_ids,
    preview_x_bookmark_destination_reconciliation,
    repair_x_bookmark_destinations,
)


def test_repair_moves_save_and_ledger_to_canonical_content(db_session, test_user) -> None:
    canonical = Content(
        content_type="article",
        url="https://example.com/canonical",
        title="Canonical article",
        status="completed",
        content_metadata={"source": "self"},
    )
    shell = Content(
        content_type="article",
        url="https://x.com/i/status/101",
        title="Bookmark shell",
        status="skipped",
        content_metadata={"canonical_content_id": None},
    )
    connection = UserIntegrationConnection(
        user_id=test_user.id,
        provider="x",
        provider_user_id="42",
        is_active=True,
    )
    db_session.add_all([canonical, shell, connection])
    db_session.flush()
    shell.content_metadata = {"canonical_content_id": canonical.id}
    synced_item = UserIntegrationSyncedItem(
        connection_id=connection.id,
        channel="bookmarks",
        external_item_id="101",
        content_id=shell.id,
        item_url="https://x.com/i/status/101",
    )
    db_session.add_all(
        [
            synced_item,
            ContentKnowledgeSave(user_id=test_user.id, content_id=shell.id),
            ContentKnowledgeSave(user_id=test_user.id, content_id=canonical.id),
        ]
    )
    db_session.commit()

    plans = preview_x_bookmark_destination_reconciliation(
        db_session,
        user_id=test_user.id,
    )

    assert len(plans) == 1
    assert plans[0].destination_content_id == canonical.id
    assert plans[0].has_stale_bookmark_save is True
    assert plans[0].needs_ledger_update is True

    _plans, results = repair_x_bookmark_destinations(
        db_session,
        user_id=test_user.id,
    )

    db_session.refresh(synced_item)
    assert len(results) == 1
    assert results[0].destination_content_id == canonical.id
    assert results[0].stale_knowledge_save_removed is True
    assert synced_item.content_id == canonical.id
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=test_user.id, content_id=canonical.id)
        .one()
    )
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=test_user.id, content_id=shell.id)
        .first()
        is None
    )

    repaired_plans = preview_x_bookmark_destination_reconciliation(
        db_session,
        user_id=test_user.id,
    )
    assert repaired_plans == []


def test_x_bookmark_provenance_is_scoped_per_user(db_session, test_user) -> None:
    content = Content(
        content_type="article",
        url="https://example.com/shared",
        title="Shared content",
        status="completed",
        content_metadata={"tweet_snapshot_source": "x_bookmarks_sync"},
    )
    x_connection = UserIntegrationConnection(
        user_id=test_user.id,
        provider="x",
        provider_user_id="42",
        is_active=True,
    )
    db_session.add_all([content, x_connection])
    db_session.flush()
    content_id = content.id
    assert content_id is not None
    db_session.add(
        UserIntegrationSyncedItem(
            connection_id=x_connection.id,
            channel="bookmarks",
            external_item_id="101",
            content_id=content_id,
            item_url="https://x.com/i/status/101",
        )
    )
    db_session.commit()

    assert list_x_bookmark_destination_content_ids(
        db_session,
        user_id=test_user.id,
        content_ids=[content_id],
    ) == {content_id}
    assert (
        list_x_bookmark_destination_content_ids(
            db_session,
            user_id=test_user.id + 1,
            content_ids=[content_id],
        )
        == set()
    )


def test_repair_limit_counts_candidates_not_clean_ledger_rows(db_session, test_user) -> None:
    clean_content = Content(
        content_type="article",
        url="https://example.com/clean",
        status="completed",
        content_metadata={},
    )
    missing_save_content = Content(
        content_type="article",
        url="https://example.com/missing-save",
        status="completed",
        content_metadata={},
    )
    connection = UserIntegrationConnection(
        user_id=test_user.id,
        provider="x",
        provider_user_id="42",
        is_active=True,
    )
    db_session.add_all([clean_content, missing_save_content, connection])
    db_session.flush()
    clean_item = UserIntegrationSyncedItem(
        connection_id=connection.id,
        channel="bookmarks",
        external_item_id="clean",
        content_id=clean_content.id,
    )
    repair_item = UserIntegrationSyncedItem(
        connection_id=connection.id,
        channel="bookmarks",
        external_item_id="repair",
        content_id=missing_save_content.id,
    )
    db_session.add_all(
        [
            clean_item,
            repair_item,
            ContentKnowledgeSave(user_id=test_user.id, content_id=clean_content.id),
        ]
    )
    db_session.commit()

    plans = preview_x_bookmark_destination_reconciliation(
        db_session,
        user_id=test_user.id,
        limit=1,
    )

    assert len(plans) == 1
    assert plans[0].synced_item_id == repair_item.id
