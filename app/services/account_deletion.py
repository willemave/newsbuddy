"""Complete, retry-safe removal of one user account and its owned data."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import cast, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.contracts import TaskStatus
from app.models.db import (
    AgentDataFile,
    AnalyticsInteraction,
    AudioEpisode,
    BriefingLens,
    BriefingPendingSource,
    BriefingSegment,
    BriefingState,
    ChatMessage,
    ChatSession,
    CliLinkSession,
    ConsumedRefreshToken,
    Content,
    ContentKnowledgeSave,
    ContentReadStatus,
    ContentStatusEntry,
    ContentUnlikes,
    FeedDiscoveryRun,
    FeedDiscoverySuggestion,
    LearningDeck,
    LearningDeckRun,
    LlmTask,
    LlmTaskAction,
    NewsItem,
    NewsItemDiscussion,
    NewsItemReadStatus,
    OnboardingDiscoveryLane,
    OnboardingDiscoveryRun,
    OnboardingDiscoverySuggestion,
    OnboardingFirstEditionRun,
    OnboardingFirstEditionSource,
    ProcessingTask,
    ProcessingTaskUserAccess,
    User,
    UserApiKey,
    UserFeedback,
    UserIntegrationConnection,
    UserIntegrationSyncedItem,
    UserIntegrationSyncState,
    UserScraperConfig,
    VendorUsageRecord,
)
from app.models.metadata.state import remove_user_references
from app.services.agent_data_sync import get_agent_data_user_root
from app.services.learning_deck_artifacts import delete_learning_deck_objects
from app.services.personal_markdown_library import get_personal_markdown_user_root
from app.services.token_crypto import decrypt_token
from app.services.x_api import revoke_oauth_token

logger = get_logger(__name__)

# This registry is deliberately explicit. Its contract test compares it with the
# SQLAlchemy model registry so adding a new user-owned table requires a deletion decision.
USER_OWNED_MODELS: tuple[tuple[type, str], ...] = (
    (AnalyticsInteraction, "user_id"),
    (AgentDataFile, "user_id"),
    (UserApiKey, "user_id"),
    (ConsumedRefreshToken, "user_id"),
    (ProcessingTaskUserAccess, "user_id"),
    (ProcessingTask, "owner_user_id"),
    (AudioEpisode, "user_id"),
    (BriefingLens, "user_id"),
    (BriefingSegment, "user_id"),
    (BriefingPendingSource, "user_id"),
    (BriefingState, "user_id"),
    (OnboardingFirstEditionRun, "user_id"),
    (ChatSession, "user_id"),
    (ContentReadStatus, "user_id"),
    (ContentKnowledgeSave, "user_id"),
    (ContentUnlikes, "user_id"),
    (ContentStatusEntry, "user_id"),
    (FeedDiscoveryRun, "user_id"),
    (FeedDiscoverySuggestion, "user_id"),
    (UserFeedback, "user_id"),
    (UserIntegrationConnection, "user_id"),
    (LearningDeckRun, "user_id"),
    (LearningDeck, "user_id"),
    (LlmTask, "user_id"),
    (NewsItem, "owner_user_id"),
    (NewsItemReadStatus, "user_id"),
    (OnboardingDiscoveryRun, "user_id"),
    (OnboardingDiscoverySuggestion, "user_id"),
    (UserScraperConfig, "user_id"),
    (VendorUsageRecord, "user_id"),
)


def cancel_pending_user_tasks(db: Session, *, user_id: int, current_task_id: int) -> bool:
    """Cancel queued work and report whether another user task is still running."""
    db.query(ProcessingTask).filter(
        ProcessingTask.id != current_task_id,
        ProcessingTask.owner_user_id == user_id,
        ProcessingTask.status == TaskStatus.PENDING.value,
    ).delete(synchronize_session=False)
    active_exists = (
        db.query(ProcessingTask.id)
        .filter(
            ProcessingTask.id != current_task_id,
            ProcessingTask.owner_user_id == user_id,
            ProcessingTask.status == TaskStatus.PROCESSING.value,
        )
        .first()
        is not None
    )
    return not active_exists


def purge_user_account(db: Session, *, user_id: int, current_task_id: int) -> None:
    """Remove all registered account rows, external grants, and user files."""
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if user is None:
        _scrub_deletion_task(db, current_task_id=current_task_id)
        db.commit()
        return

    _destroy_agent_vm(user)
    _revoke_x_connections(db, user_id=user_id)
    _delete_user_files(db, user_id=user_id)
    _delete_indirect_rows(db, user_id=user_id)
    _scrub_shared_content_metadata(db, user_id=user_id)

    for model, column_name in USER_OWNED_MODELS:
        column = getattr(model, column_name)
        db.query(model).filter(column == user_id).delete(synchronize_session=False)

    db.query(CliLinkSession).filter(CliLinkSession.approved_by_user_id == user_id).update(
        {CliLinkSession.approved_by_user_id: None}, synchronize_session=False
    )
    db.query(LlmTaskAction).filter(LlmTaskAction.approved_by_user_id == user_id).update(
        {LlmTaskAction.approved_by_user_id: None}, synchronize_session=False
    )
    db.query(UserApiKey).filter(UserApiKey.created_by_admin_user_id == user_id).update(
        {UserApiKey.created_by_admin_user_id: None}, synchronize_session=False
    )
    db.delete(user)
    _scrub_deletion_task(db, current_task_id=current_task_id)
    db.commit()


def _delete_indirect_rows(db: Session, *, user_id: int) -> None:
    session_ids = [
        row[0] for row in db.query(ChatSession.id).filter(ChatSession.user_id == user_id)
    ]
    if session_ids:
        db.query(ChatMessage).filter(ChatMessage.session_id.in_(session_ids)).delete(
            synchronize_session=False
        )

    connection_ids = [
        row[0]
        for row in db.query(UserIntegrationConnection.id).filter(
            UserIntegrationConnection.user_id == user_id
        )
    ]
    if connection_ids:
        db.query(UserIntegrationSyncState).filter(
            UserIntegrationSyncState.connection_id.in_(connection_ids)
        ).delete(synchronize_session=False)
        db.query(UserIntegrationSyncedItem).filter(
            UserIntegrationSyncedItem.connection_id.in_(connection_ids)
        ).delete(synchronize_session=False)

    onboarding_run_ids = [
        row[0]
        for row in db.query(OnboardingDiscoveryRun.id).filter(
            OnboardingDiscoveryRun.user_id == user_id
        )
    ]
    if onboarding_run_ids:
        db.query(OnboardingDiscoveryLane).filter(
            OnboardingDiscoveryLane.run_id.in_(onboarding_run_ids)
        ).delete(synchronize_session=False)

    edition_run_ids = [
        row[0]
        for row in db.query(OnboardingFirstEditionRun.id).filter(
            OnboardingFirstEditionRun.user_id == user_id
        )
    ]
    if edition_run_ids:
        db.query(OnboardingFirstEditionSource).filter(
            OnboardingFirstEditionSource.run_id.in_(edition_run_ids)
        ).delete(synchronize_session=False)

    llm_task_ids = [row[0] for row in db.query(LlmTask.id).filter(LlmTask.user_id == user_id)]
    if llm_task_ids:
        db.query(LlmTaskAction).filter(LlmTaskAction.llm_task_id.in_(llm_task_ids)).delete(
            synchronize_session=False
        )

    owned_news_ids = [
        row[0] for row in db.query(NewsItem.id).filter(NewsItem.owner_user_id == user_id)
    ]
    if owned_news_ids:
        db.query(NewsItemDiscussion).filter(
            NewsItemDiscussion.news_item_id.in_(owned_news_ids)
        ).delete(synchronize_session=False)


def _scrub_shared_content_metadata(db: Session, *, user_id: int) -> None:
    """Remove private user references retained on otherwise shared content rows."""

    metadata = cast(Content.content_metadata, JSONB)
    processing = metadata["processing"]
    candidates = (
        db.query(Content)
        .filter(
            or_(
                metadata["submitted_by_user_id"].as_integer() == user_id,
                metadata["share_and_chat_user_ids"].contains([user_id]),
                metadata["share_and_chat_requests"].contains([{"user_id": user_id}]),
                processing["submitted_by_user_id"].as_integer() == user_id,
                processing["share_and_chat_user_ids"].contains([user_id]),
                processing["share_and_chat_requests"].contains([{"user_id": user_id}]),
            )
        )
        .all()
    )
    for content in candidates:
        content.content_metadata = remove_user_references(
            content.content_metadata,
            user_id=user_id,
        )


def _revoke_x_connections(db: Session, *, user_id: int) -> None:
    connections = (
        db.query(UserIntegrationConnection)
        .filter(UserIntegrationConnection.user_id == user_id)
        .all()
    )
    for connection in connections:
        encrypted = connection.refresh_token_encrypted or connection.access_token_encrypted
        if not encrypted:
            continue
        try:
            revoke_oauth_token(
                token=decrypt_token(encrypted),
                token_type_hint=(
                    "refresh_token" if connection.refresh_token_encrypted else "access_token"
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Unable to revoke X during account deletion; local credentials will be purged",
                extra={"component": "account_deletion", "operation": "revoke_x"},
            )


def _delete_user_files(db: Session, *, user_id: int) -> None:
    audio_paths = [
        Path(row[0])
        for row in db.query(AudioEpisode.audio_storage_path).filter(
            AudioEpisode.user_id == user_id,
            AudioEpisode.audio_storage_path.is_not(None),
        )
        if row[0]
    ]
    for path in audio_paths:
        path.unlink(missing_ok=True)

    object_keys: list[str] = []
    for (keys,) in db.query(LearningDeck.artifact_object_keys).filter(
        LearningDeck.user_id == user_id
    ):
        if isinstance(keys, list):
            object_keys.extend(key for key in keys if isinstance(key, str))
    for (keys,) in db.query(LearningDeckRun.artifact_object_keys).filter(
        LearningDeckRun.user_id == user_id
    ):
        if isinstance(keys, list):
            object_keys.extend(key for key in keys if isinstance(key, str))
    if object_keys:
        delete_learning_deck_objects(object_keys)

    user_root = get_personal_markdown_user_root(user_id)
    if user_root.exists():
        shutil.rmtree(user_root)
    agent_data_root = get_agent_data_user_root(user_id)
    if agent_data_root.exists():
        shutil.rmtree(agent_data_root)


def _destroy_agent_vm(user: User) -> None:
    """Destroy external per-user compute and recovery state before deletion."""
    from app.core.settings import get_settings

    settings = get_settings()
    api_key = settings.llm_task_sandbox_e2b_api_key
    sandbox_id = str(user.agent_vm_sandbox_id or "").strip()
    snapshot_id = str(user.agent_vm_snapshot_id or "").strip()
    if not sandbox_id and not snapshot_id:
        return
    if not api_key:
        raise RuntimeError("E2B_API_KEY is required to destroy the user's external sandbox data")
    if sandbox_id:
        try:
            from e2b_code_interpreter import Sandbox

            Sandbox.kill(sandbox_id, api_key=api_key)
        except Exception as exc:  # noqa: BLE001
            if "not found" not in str(exc).lower():
                raise
    if snapshot_id:
        try:
            from e2b_code_interpreter import Sandbox

            Sandbox.delete_snapshot(snapshot_id, api_key=api_key)
        except Exception as exc:  # noqa: BLE001
            if "not found" not in str(exc).lower():
                raise


def _scrub_deletion_task(db: Session, *, current_task_id: int) -> None:
    task = db.query(ProcessingTask).filter(ProcessingTask.id == current_task_id).first()
    if task is not None:
        task.payload = {}
        task.dedupe_key = None
