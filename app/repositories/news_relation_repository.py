from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Query, Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.contracts import NewsItemStatus, NewsItemVisibilityScope
from app.models.db import NewsItem
from app.repositories.news_search_expressions import news_relation_title_document_expression


def list_exact_relation_candidates(
    db: Session,
    *,
    item: NewsItem,
    lookback_floor: datetime,
    exact_key: tuple[str, str],
) -> list[NewsItem]:
    filters = _exact_candidate_filters(item, exact_key=exact_key)
    if not filters:
        return []
    return (
        _relation_candidate_query(db, item=item, lookback_floor=lookback_floor)
        .filter(or_(*filters))
        .order_by(NewsItem.ingested_at.desc(), NewsItem.id.desc())
        .all()
    )


def list_ranked_relation_candidates(
    db: Session,
    *,
    item: NewsItem,
    lookback_floor: datetime,
    item_tokens: set[str],
    limit: int,
) -> list[NewsItem]:
    if not item_tokens:
        return []

    title_document = news_relation_title_document_expression()
    search_query = func.to_tsquery("english", " | ".join(sorted(item_tokens)))
    rank = func.ts_rank_cd(title_document, search_query)
    return (
        _relation_candidate_query(db, item=item, lookback_floor=lookback_floor)
        .filter(title_document.op("@@")(search_query))
        .order_by(rank.desc(), NewsItem.ingested_at.desc(), NewsItem.id.desc())
        .limit(limit)
        .all()
    )


def _relation_candidate_query(
    db: Session,
    *,
    item: NewsItem,
    lookback_floor: datetime,
) -> Query[NewsItem]:
    query = (
        db.query(NewsItem)
        .filter(NewsItem.status == NewsItemStatus.READY.value)
        .filter(NewsItem.representative_news_item_id.is_(None))
        .filter(NewsItem.id != item.id)
        .filter(NewsItem.visibility_scope == item.visibility_scope)
        .filter(NewsItem.ingested_at >= lookback_floor)
    )
    if item.visibility_scope == NewsItemVisibilityScope.USER.value:
        return query.filter(NewsItem.owner_user_id == item.owner_user_id)
    return query.filter(NewsItem.owner_user_id.is_(None))


def _exact_candidate_filters(
    item: NewsItem,
    *,
    exact_key: tuple[str, str],
) -> list[ColumnElement[bool]]:
    key_kind, key_value = exact_key
    if key_kind in {"story", "item"}:
        url_suffix = key_value.removeprefix("https://")
        url_variants = {
            key_value,
            f"http://{url_suffix}",
            url_suffix,
            f"//{url_suffix}",
        }
        if key_kind == "story":
            story_key = func.coalesce(NewsItem.canonical_story_url, NewsItem.article_url)
            return [story_key.in_(url_variants)]
        item_key = func.coalesce(NewsItem.canonical_item_url, NewsItem.discussion_url)
        return [item_key.in_(url_variants)]
    if key_kind == "external" and item.platform and item.source_external_id:
        return [
            and_(
                NewsItem.platform == item.platform,
                NewsItem.source_external_id == item.source_external_id,
            )
        ]
    return []
