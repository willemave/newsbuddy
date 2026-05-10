from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.core.db import Base
from app.core.logging import get_logger
from app.models.contracts import (
    NewsItemStatus,
    NewsItemVisibilityScope,
)
from app.models.db.common import _utcnow
from app.utils.news_titles import (
    get_news_article_title,
    get_news_summary_title,
    set_news_article_title,
    set_news_summary_title,
)

logger = get_logger(__name__)


class NewsItem(Base):
    """Short-form news item used by the news feed pipeline."""

    __tablename__ = "news_items"

    id = Column(Integer, primary_key=True)
    ingest_key = Column(String(128), nullable=False, index=True)
    visibility_scope = Column(
        String(20),
        nullable=False,
        default=NewsItemVisibilityScope.GLOBAL.value,
        index=True,
    )
    owner_user_id = Column(Integer, nullable=True, index=True)
    platform = Column(String(50), nullable=True, index=True)
    source_type = Column(String(50), nullable=True, index=True)
    source_label = Column(String(255), nullable=True)
    source_external_id = Column(String(255), nullable=True, index=True)
    user_scraper_config_id = Column(Integer, nullable=True, index=True)
    user_integration_connection_id = Column(Integer, nullable=True, index=True)
    canonical_item_url = Column(String(2048), nullable=True)
    canonical_story_url = Column(String(2048), nullable=True, index=True)
    article_url = Column(String(2048), nullable=True)
    article_domain = Column(String(255), nullable=True)
    discussion_url = Column(String(2048), nullable=True)
    summary_key_points = Column(JSON, default=list, nullable=False)
    summary_text = Column(Text, nullable=True)
    raw_metadata = Column(JSON, default=dict, nullable=False)
    status = Column(String(20), nullable=False, default=NewsItemStatus.NEW.value, index=True)
    legacy_content_id = Column(Integer, nullable=True, index=True)
    representative_news_item_id = Column(Integer, nullable=True, index=True)
    cluster_size = Column(Integer, nullable=False, default=1)
    enrichment_updated_at = Column(DateTime, nullable=True, index=True)
    published_at = Column(DateTime, nullable=True, index=True)
    ingested_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    processed_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("ingest_key", name="uq_news_items_ingest_key"),
        UniqueConstraint("legacy_content_id", name="uq_news_items_legacy_content_id"),
        Index(
            "idx_news_items_visibility_owner_status", "visibility_scope", "owner_user_id", "status"
        ),
        Index("idx_news_items_status_ingested", "status", "ingested_at"),
        Index("idx_news_items_owner_ingested", "owner_user_id", "ingested_at"),
        Index(
            "idx_news_items_visible_feed",
            "visibility_scope",
            "owner_user_id",
            "representative_news_item_id",
            "status",
            "ingested_at",
        ),
    )

    def __init__(self, **kwargs: Any) -> None:
        article_title = kwargs.pop("article_title", None)
        summary_title = kwargs.pop("summary_title", None)
        super().__init__(**kwargs)
        if article_title is not None and self.article_title is None:
            self.article_title = article_title
        if summary_title is not None and self.summary_title is None:
            self.summary_title = summary_title

    @property
    def article_title(self) -> str | None:
        return get_news_article_title(self.raw_metadata)

    @article_title.setter
    def article_title(self, value: Any) -> None:
        self.raw_metadata = set_news_article_title(self.raw_metadata, value)

    @property
    def summary_title(self) -> str | None:
        return get_news_summary_title(self.raw_metadata)

    @summary_title.setter
    def summary_title(self, value: Any) -> None:
        self.raw_metadata = set_news_summary_title(self.raw_metadata, value)


class NewsItemDiscussion(Base):
    """Latest discussion comments and summary for one short-form news item."""

    __tablename__ = "news_item_discussions"

    id = Column(Integer, primary_key=True)
    news_item_id = Column(Integer, nullable=False)
    platform = Column(String(50), nullable=False)
    external_id = Column(String(255), nullable=True)
    discussion_url = Column(String(2048), nullable=True)
    title = Column(String(500), nullable=True)
    author = Column(String(255), nullable=True)
    score = Column(Integer, nullable=True)
    comment_count = Column(Integer, nullable=True)
    raw_comments_ref = Column(JSON, nullable=True)
    raw_comments_sha256 = Column(String(64), nullable=True)
    fetched_comment_count = Column(Integer, nullable=True)
    last_count_checked_at = Column(DateTime, nullable=True)
    last_comments_fetched_at = Column(DateTime, nullable=True)
    next_refresh_after = Column(DateTime, nullable=True)
    summary = Column(JSON, nullable=True)
    summary_status = Column(String(20), nullable=False, default="not_ready")
    summary_version = Column(Integer, nullable=True)
    summary_model = Column(String(100), nullable=True)
    summary_generated_at = Column(DateTime, nullable=True)
    last_refresh_status = Column(String(20), nullable=False, default="pending")
    last_refresh_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("news_item_id", name="uq_news_item_discussions_news_item"),
        Index("idx_news_item_discussions_platform_external", "platform", "external_id"),
        Index("idx_news_item_discussions_next_refresh", "next_refresh_after"),
        Index("idx_news_item_discussions_status", "last_refresh_status"),
    )


class NewsItemReadStatus(Base):
    """Track which visible news items have been read by which user."""

    __tablename__ = "news_item_read_status"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    news_item_id = Column(Integer, nullable=False, index=True)
    read_at = Column(DateTime, default=_utcnow, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_news_item_read_status_user_item", "user_id", "news_item_id", unique=True),
    )
