"""Shared builders for model-based test fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.contracts import ContentStatus, ContentType
from app.models.db import (
    ChatSession,
    Content,
    ContentKnowledgeSave,
    ContentReadStatus,
    ContentStatusEntry,
    NewsItem,
    ProcessingTask,
    UserIntegrationConnection,
)
from app.models.db.users import User
from app.models.domain.content import ContentData


def _model_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def persist_row(db_session: Session, row: Any) -> Any:
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def default_content_metadata(*, title: str, content_type: str) -> dict[str, Any]:
    """Build list/detail-friendly default metadata for common content types."""
    del title, content_type
    return {}


def build_user_row(*, index: int = 1, **overrides: Any) -> User:
    user = User(
        apple_id=overrides.pop("apple_id", f"test.apple.{index}"),
        email=overrides.pop("email", f"user{index}@example.com"),
        full_name=overrides.pop("full_name", f"Test User {index}"),
        is_active=overrides.pop("is_active", True),
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


def create_user_row(db_session: Session, *, index: int = 1, **overrides: Any) -> User:
    return persist_row(db_session, build_user_row(index=index, **overrides))


def build_content_row(*, index: int = 1, **overrides: Any) -> Content:
    content_type = _model_value(overrides.pop("content_type", ContentType.ARTICLE))
    title = overrides.pop("title", f"Test Content {index}")
    content = Content(
        content_type=content_type,
        url=overrides.pop("url", f"https://example.com/content/{index}"),
        source_url=overrides.pop("source_url", None),
        title=title,
        source=overrides.pop("source", "example.com"),
        status=_model_value(overrides.pop("status", ContentStatus.COMPLETED)),
        platform=overrides.pop("platform", None),
        classification=overrides.pop("classification", None),
        publication_date=overrides.pop("publication_date", None),
        content_metadata=overrides.pop(
            "content_metadata",
            default_content_metadata(title=title, content_type=content_type),
        ),
    )
    for key, value in overrides.items():
        setattr(content, key, value)
    return content


def create_content_row(db_session: Session, *, index: int = 1, **overrides: Any) -> Content:
    return persist_row(db_session, build_content_row(index=index, **overrides))


def build_content_data(*, index: int = 1, **overrides: Any) -> ContentData:
    content_type = _model_value(overrides.pop("content_type", ContentType.ARTICLE))
    title = overrides.pop("title", f"Test Content {index}")
    url = overrides.pop("url", f"https://example.com/content/{index}")
    return ContentData(
        id=overrides.pop("id", None),
        content_type=ContentType(content_type),
        url=url,
        source_url=overrides.pop("source_url", url),
        title=title,
        status=ContentStatus(_model_value(overrides.pop("status", ContentStatus.NEW))),
        metadata=overrides.pop(
            "metadata",
            default_content_metadata(title=title, content_type=content_type),
        ),
        platform=overrides.pop("platform", None),
        source=overrides.pop("source", "example.com"),
        error_message=overrides.pop("error_message", None),
        retry_count=overrides.pop("retry_count", 0),
        created_at=overrides.pop("created_at", None),
        processed_at=overrides.pop("processed_at", None),
        publication_date=overrides.pop("publication_date", None),
        **overrides,
    )


def _resolved_id(row: Any | None, explicit_id: int | None) -> int | None:
    if explicit_id is not None:
        return explicit_id
    return getattr(row, "id", None) if row is not None else None


def build_content_status_entry_row(
    *,
    user: User | None = None,
    user_id: int | None = None,
    content: Content | None = None,
    content_id: int | None = None,
    status: str = "inbox",
    **overrides: Any,
) -> ContentStatusEntry:
    return ContentStatusEntry(
        user_id=_resolved_id(user, user_id),
        content_id=_resolved_id(content, content_id),
        status=status,
        **overrides,
    )


def create_content_status_entry_row(
    db_session: Session,
    **overrides: Any,
) -> ContentStatusEntry:
    return persist_row(db_session, build_content_status_entry_row(**overrides))


def build_content_knowledge_save_row(
    *,
    user: User | None = None,
    user_id: int | None = None,
    content: Content | None = None,
    content_id: int | None = None,
    **overrides: Any,
) -> ContentKnowledgeSave:
    return ContentKnowledgeSave(
        user_id=_resolved_id(user, user_id),
        content_id=_resolved_id(content, content_id),
        **overrides,
    )


def create_content_knowledge_save_row(
    db_session: Session,
    **overrides: Any,
) -> ContentKnowledgeSave:
    return persist_row(db_session, build_content_knowledge_save_row(**overrides))


def build_content_read_status_row(
    *,
    user: User | None = None,
    user_id: int | None = None,
    content: Content | None = None,
    content_id: int | None = None,
    **overrides: Any,
) -> ContentReadStatus:
    return ContentReadStatus(
        user_id=_resolved_id(user, user_id),
        content_id=_resolved_id(content, content_id),
        **overrides,
    )


def create_content_read_status_row(
    db_session: Session,
    **overrides: Any,
) -> ContentReadStatus:
    return persist_row(db_session, build_content_read_status_row(**overrides))


def build_chat_session_row(
    *,
    index: int = 1,
    user: User | None = None,
    user_id: int | None = None,
    content: Content | None = None,
    content_id: int | None = None,
    **overrides: Any,
) -> ChatSession:
    session = ChatSession(
        user_id=_resolved_id(user, user_id),
        content_id=_resolved_id(content, content_id),
        title=overrides.pop("title", f"Chat Session {index}"),
        session_type=overrides.pop("session_type", "knowledge_chat"),
        llm_model=overrides.pop("llm_model", "openai:gpt-5.5"),
        llm_provider=overrides.pop("llm_provider", "openai"),
        topic=overrides.pop("topic", None),
        context_snapshot=overrides.pop("context_snapshot", None),
    )
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


def create_chat_session_row(
    db_session: Session,
    *,
    index: int = 1,
    **overrides: Any,
) -> ChatSession:
    return persist_row(db_session, build_chat_session_row(index=index, **overrides))


def build_processing_task_row(
    *,
    content: Content | None = None,
    content_id: int | None = None,
    task_type: str = "analyze_url",
    payload: dict[str, Any] | None = None,
    status: str = "pending",
    queue_name: str = "content",
    **overrides: Any,
) -> ProcessingTask:
    return ProcessingTask(
        task_type=_model_value(task_type),
        content_id=_resolved_id(content, content_id),
        payload=payload or {},
        status=_model_value(status),
        queue_name=_model_value(queue_name),
        **overrides,
    )


def create_processing_task_row(
    db_session: Session,
    **overrides: Any,
) -> ProcessingTask:
    return persist_row(db_session, build_processing_task_row(**overrides))


def default_news_item_metadata(*, title: str, ingest_key: str) -> dict[str, Any]:
    """Build router-visible default metadata for one news item."""
    return {
        "cluster": {
            "member_ids": [ingest_key],
            "source_labels": ["Hacker News"],
            "domains": ["example.com"],
            "discussion_snippets": ["Useful comment"],
            "related_titles": [title],
            "latest_member_ingested_at": datetime.now(UTC).isoformat(),
        }
    }


def build_news_item_row(*, index: int = 1, **overrides: Any) -> NewsItem:
    ingest_key = overrides.pop("ingest_key", f"news-item-{index}")
    title = overrides.pop("article_title", f"News Story {index}")
    summary_title = overrides.pop("summary_title", title)
    canonical_story_url = overrides.pop(
        "canonical_story_url",
        f"https://example.com/story-{index}",
    )
    article_url = overrides.pop("article_url", canonical_story_url)
    discussion_url = overrides.pop(
        "discussion_url",
        f"https://news.ycombinator.com/item?id={1000 + index}",
    )
    source_external_id = overrides.pop("source_external_id", ingest_key)
    ingested_at = overrides.pop("ingested_at", datetime.now(UTC).replace(tzinfo=None))
    raw_metadata = default_news_item_metadata(title=summary_title, ingest_key=ingest_key)
    raw_metadata.update(overrides.pop("raw_metadata", {}))

    item = NewsItem(
        ingest_key=ingest_key,
        visibility_scope=overrides.pop("visibility_scope", "global"),
        owner_user_id=overrides.pop("owner_user_id", None),
        platform=overrides.pop("platform", "hackernews"),
        source_type=overrides.pop("source_type", "hackernews"),
        source_label=overrides.pop("source_label", "Hacker News"),
        source_external_id=source_external_id,
        canonical_item_url=overrides.pop("canonical_item_url", discussion_url),
        canonical_story_url=canonical_story_url,
        article_url=article_url,
        article_title=title,
        article_domain=overrides.pop("article_domain", "example.com"),
        discussion_url=discussion_url,
        summary_title=summary_title,
        summary_key_points=overrides.pop("summary_key_points", ["Point one"]),
        summary_text=overrides.pop("summary_text", f"{summary_title} summary"),
        raw_metadata=raw_metadata,
        status=overrides.pop("status", "ready"),
        representative_news_item_id=overrides.pop("representative_news_item_id", None),
        cluster_size=overrides.pop("cluster_size", 1),
        published_at=overrides.pop("published_at", None),
        ingested_at=ingested_at,
        processed_at=overrides.pop("processed_at", ingested_at),
    )
    for key, value in overrides.items():
        setattr(item, key, value)
    return item


def create_news_item_row(db_session: Session, *, index: int = 1, **overrides: Any) -> NewsItem:
    return persist_row(db_session, build_news_item_row(index=index, **overrides))


def build_integration_connection_row(
    *,
    index: int = 1,
    user: User | None = None,
    user_id: int | None = None,
    provider: str = "x",
    **overrides: Any,
) -> UserIntegrationConnection:
    connection = UserIntegrationConnection(
        user_id=_resolved_id(user, user_id),
        provider=provider,
        provider_user_id=overrides.pop("provider_user_id", f"{provider}-user-{index}"),
        provider_username=overrides.pop("provider_username", f"{provider}_user_{index}"),
        scopes=overrides.pop("scopes", []),
        connection_metadata=overrides.pop("connection_metadata", {}),
        is_active=overrides.pop("is_active", True),
    )
    for key, value in overrides.items():
        setattr(connection, key, value)
    return connection


def create_integration_connection_row(
    db_session: Session,
    *,
    index: int = 1,
    **overrides: Any,
) -> UserIntegrationConnection:
    return persist_row(db_session, build_integration_connection_row(index=index, **overrides))
