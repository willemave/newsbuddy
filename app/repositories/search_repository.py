"""Repository entry points for content and news search."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import String, and_, cast, func, literal, literal_column, or_
from sqlalchemy.orm import Session

from app.models.contracts import NewsItemStatus, NewsItemVisibilityScope
from app.models.db import Content, NewsItem, NewsItemReadStatus, UserScraperConfig
from app.repositories.content_feed_query import apply_sort_timestamp_cursor, build_user_feed_query

SUBSCRIPTION_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "article",
    "articles",
    "episode",
    "episodes",
    "feed",
    "feeds",
    "have",
    "in",
    "inbox",
    "my",
    "newsletter",
    "newsletters",
    "of",
    "pod",
    "pods",
    "podcast",
    "podcasts",
    "read",
    "series",
    "show",
    "shows",
    "the",
}
SUBSCRIPTION_QUERY_HINTS = {
    "episode",
    "episodes",
    "feed",
    "feeds",
    "pod",
    "pods",
    "podcast",
    "podcasts",
    "series",
    "show",
    "shows",
}


def _uses_postgres(db: Session) -> bool:
    bind = db.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


def _query_tokens(query_text: str, *, min_length: int) -> list[str]:
    return [
        token for token in re.findall(r"[a-z0-9]+", query_text.lower()) if len(token) >= min_length
    ]


def _content_summary_title_expr():
    return func.coalesce(
        cast(Content.content_metadata["summary"]["title"].as_string(), String),
        "",
    )


def _content_title_expr():
    return func.coalesce(cast(Content.title, String), "")


def _content_source_expr():
    return func.coalesce(cast(Content.source, String), "")


def _content_search_text_expr():
    return func.coalesce(cast(Content.search_text, String), "")


def _apply_postgres_content_search(query, query_text: str, *, context: dict[str, Any]):
    normalized = " ".join(query_text.split()).strip()
    if not normalized:
        return query

    summary_title_vector = func.setweight(
        func.to_tsvector("english", _content_summary_title_expr()),
        literal_column("'A'"),
    )
    stored_title_vector = func.setweight(
        func.to_tsvector("english", _content_title_expr()),
        literal_column("'B'"),
    )
    source_vector = func.setweight(
        func.to_tsvector("english", _content_source_expr()),
        literal_column("'C'"),
    )
    search_text_vector = func.setweight(
        func.to_tsvector("english", _content_search_text_expr()),
        literal_column("'D'"),
    )
    search_document = (
        summary_title_vector.op("||")(stored_title_vector)
        .op("||")(source_vector)
        .op("||")(search_text_vector)
    )

    search_query = func.websearch_to_tsquery("english", normalized)
    search_rank = func.ts_rank_cd(search_document, search_query)
    summary_title_match = _content_summary_title_expr().bool_op("OPERATOR(public.%)")(normalized)
    stored_title_match = _content_title_expr().bool_op("OPERATOR(public.%)")(normalized)
    source_match = _content_source_expr().bool_op("OPERATOR(public.%)")(normalized)
    trigram_rank = func.greatest(
        func.public.word_similarity(normalized, _content_summary_title_expr()),
        func.public.word_similarity(normalized, _content_title_expr()),
        func.public.word_similarity(normalized, _content_source_expr()),
    )
    combined_filter = or_(
        search_document.op("@@")(search_query),
        summary_title_match,
        stored_title_match,
        source_match,
        trigram_rank >= 0.5,
    )
    context["rank_expr"] = func.greatest(search_rank, trigram_rank * 0.25)
    return query.filter(combined_filter)


def _apply_generic_content_search(query, query_text: str, *, context: dict[str, Any]):
    del context
    tokens = _query_tokens(query_text, min_length=2)
    if not tokens:
        return query

    token_filters = [
        or_(
            func.lower(_content_summary_title_expr()).like(f"%{token}%"),
            func.lower(_content_title_expr()).like(f"%{token}%"),
            func.lower(_content_source_expr()).like(f"%{token}%"),
            func.lower(_content_search_text_expr()).like(f"%{token}%"),
        )
        for token in tokens
    ]
    return query.filter(and_(*token_filters))


def _apply_content_search(query, query_text: str, *, db: Session, context: dict[str, Any]):
    if _uses_postgres(db):
        return _apply_postgres_content_search(query, query_text, context=context)
    return _apply_generic_content_search(query, query_text, context=context)


def search_content_page(
    db: Session,
    *,
    user_id: int,
    query_text: str,
    content_type: str,
    cursor: tuple[int | None, datetime | None, float | None],
    limit: int,
    offset: int,
):
    """Return paginated visible content rows for the content search API."""
    query = build_user_feed_query(db, user_id, mode="inbox")
    search_context: dict[str, Any] = {}
    if content_type and content_type != "all":
        query = query.filter(Content.content_type == content_type)

    query = _apply_content_search(query, query_text, db=db, context=search_context)
    search_rank_expr = search_context.get("rank_expr")
    if search_rank_expr is None:
        query = query.add_columns(literal(None).label("_search_rank"))
        query = query.order_by(Content.created_at.desc(), Content.id.desc())
    else:
        query = query.add_columns(search_rank_expr.label("_search_rank"))
        query = query.order_by(
            search_rank_expr.desc(),
            Content.created_at.desc(),
            Content.id.desc(),
        )

    last_id, last_sort_timestamp, last_rank = cursor
    if last_id and last_sort_timestamp:
        if search_rank_expr is not None and last_rank is not None:
            query = query.filter(
                or_(
                    search_rank_expr < last_rank,
                    (
                        (search_rank_expr == last_rank)
                        & (
                            or_(
                                Content.created_at < last_sort_timestamp,
                                (
                                    (Content.created_at == last_sort_timestamp)
                                    & (Content.id < last_id)
                                ),
                            )
                        )
                    ),
                )
            )
        else:
            query = apply_sort_timestamp_cursor(query, last_sort_timestamp, last_id)
    elif offset > 0:
        query = query.offset(offset)

    return query.limit(limit + 1).all()


def search_content(
    db: Session,
    *,
    user_id: int,
    query_text: str,
    limit: int,
):
    """Return matching visible content rows plus a total count, or recent rows when none match."""
    base_query = build_user_feed_query(db, user_id, mode="inbox")
    normalized_query = query_text.strip()
    if not normalized_query:
        rows = base_query.order_by(Content.created_at.desc(), Content.id.desc()).limit(limit).all()
        return rows, 0

    search_context: dict[str, Any] = {}
    matched_query = _apply_content_search(
        base_query, normalized_query, db=db, context=search_context
    )
    total_matches = matched_query.order_by(None).count()
    search_rank_expr = search_context.get("rank_expr")
    if search_rank_expr is None:
        matched_query = matched_query.order_by(Content.created_at.desc(), Content.id.desc())
    else:
        matched_query = matched_query.order_by(
            search_rank_expr.desc(),
            Content.created_at.desc(),
            Content.id.desc(),
        )
    rows = matched_query.limit(limit).all()
    if rows:
        return rows, total_matches

    fallback_rows = (
        base_query.order_by(Content.created_at.desc(), Content.id.desc()).limit(limit).all()
    )
    return fallback_rows, 0


def _visible_news_item_query(
    db: Session,
    *,
    user_id: int,
):
    return (
        db.query(
            NewsItem,
            NewsItemReadStatus.id.label("is_read"),
        )
        .outerjoin(
            NewsItemReadStatus,
            and_(
                NewsItemReadStatus.news_item_id == NewsItem.id,
                NewsItemReadStatus.user_id == user_id,
            ),
        )
        .filter(NewsItem.status == NewsItemStatus.READY.value)
        .filter(NewsItem.representative_news_item_id.is_(None))
        .filter(
            or_(
                NewsItem.visibility_scope == NewsItemVisibilityScope.GLOBAL.value,
                and_(
                    NewsItem.visibility_scope == NewsItemVisibilityScope.USER.value,
                    NewsItem.owner_user_id == user_id,
                ),
            )
        )
    )


def _news_summary_title_expr():
    return func.coalesce(cast(NewsItem.raw_metadata["summary"]["title"].as_string(), String), "")


def _news_article_title_expr():
    return func.coalesce(cast(NewsItem.raw_metadata["article"]["title"].as_string(), String), "")


def _news_cluster_titles_expr():
    return func.coalesce(
        cast(NewsItem.raw_metadata["cluster"]["related_titles"].as_string(), String),
        "",
    )


def _news_summary_text_expr():
    return func.coalesce(cast(NewsItem.summary_text, String), "")


def _news_source_label_expr():
    return func.coalesce(cast(NewsItem.source_label, String), "")


def _news_article_domain_expr():
    return func.coalesce(cast(NewsItem.article_domain, String), "")


def _news_provenance_text_expr():
    return (
        _news_source_label_expr()
        + literal(" ")
        + _news_article_domain_expr()
        + literal(" ")
        + _news_cluster_titles_expr()
    )


def _apply_postgres_news_search(query, query_text: str, *, context: dict[str, Any]):
    normalized = " ".join(query_text.split()).strip()
    if not normalized:
        return query

    summary_title_vector = func.setweight(
        func.to_tsvector("english", _news_summary_title_expr()),
        literal_column("'A'"),
    )
    article_title_vector = func.setweight(
        func.to_tsvector("english", _news_article_title_expr()),
        literal_column("'B'"),
    )
    summary_text_vector = func.setweight(
        func.to_tsvector("english", _news_summary_text_expr()),
        literal_column("'C'"),
    )
    provenance_vector = func.setweight(
        func.to_tsvector("english", _news_provenance_text_expr()),
        literal_column("'D'"),
    )
    search_document = (
        summary_title_vector.op("||")(article_title_vector)
        .op("||")(summary_text_vector)
        .op("||")(provenance_vector)
    )

    search_query = func.websearch_to_tsquery("english", normalized)
    search_rank = func.ts_rank_cd(search_document, search_query)
    summary_title_match = _news_summary_title_expr().bool_op("OPERATOR(public.%)")(normalized)
    article_title_match = _news_article_title_expr().bool_op("OPERATOR(public.%)")(normalized)
    trigram_rank = func.greatest(
        func.public.word_similarity(normalized, _news_summary_title_expr()),
        func.public.word_similarity(normalized, _news_article_title_expr()),
        func.public.word_similarity(normalized, _news_source_label_expr()),
        func.public.word_similarity(normalized, _news_article_domain_expr()),
        func.public.word_similarity(normalized, _news_cluster_titles_expr()),
    )
    combined_filter = or_(
        search_document.op("@@")(search_query),
        summary_title_match,
        article_title_match,
        trigram_rank >= 0.5,
    )
    context["rank_expr"] = func.greatest(search_rank, trigram_rank * 0.25)
    return query.filter(combined_filter)


def _apply_generic_news_search(query, query_text: str, *, context: dict[str, Any]):
    del context
    tokens = _query_tokens(query_text, min_length=3)
    if not tokens:
        return query

    token_filters = [
        or_(
            func.lower(_news_summary_title_expr()).like(f"%{token}%"),
            func.lower(_news_article_title_expr()).like(f"%{token}%"),
            func.lower(_news_summary_text_expr()).like(f"%{token}%"),
            func.lower(_news_source_label_expr()).like(f"%{token}%"),
            func.lower(_news_article_domain_expr()).like(f"%{token}%"),
            func.lower(_news_cluster_titles_expr()).like(f"%{token}%"),
        )
        for token in tokens
    ]
    return query.filter(and_(*token_filters))


def _apply_news_search(query, query_text: str, *, db: Session, context: dict[str, Any]):
    if _uses_postgres(db):
        return _apply_postgres_news_search(query, query_text, context=context)
    return _apply_generic_news_search(query, query_text, context=context)


def search_news(
    db: Session,
    *,
    user_id: int,
    query_text: str,
    limit: int,
):
    """Return matching visible news-item rows plus a total count, or recent rows when none match."""
    sort_expr = func.coalesce(
        NewsItem.published_at,
        NewsItem.processed_at,
        NewsItem.ingested_at,
        NewsItem.created_at,
    )
    base_query = _visible_news_item_query(db, user_id=user_id)
    normalized_query = query_text.strip()
    if normalized_query:
        search_context: dict[str, Any] = {}
        matched_query = _apply_news_search(
            base_query, normalized_query, db=db, context=search_context
        )
        total_matches = matched_query.order_by(None).count()
        rank_expr = search_context.get("rank_expr")
        if rank_expr is not None:
            matched_query = matched_query.order_by(
                rank_expr.desc(), sort_expr.desc(), NewsItem.id.desc()
            )
        else:
            matched_query = matched_query.order_by(sort_expr.desc(), NewsItem.id.desc())
        rows = matched_query.limit(limit).all()
        if rows:
            return rows, total_matches

    rows = base_query.order_by(sort_expr.desc(), NewsItem.id.desc()).limit(limit).all()
    return rows, 0


def _tokenize_subscription_query(value: str | None) -> list[str]:
    if not value:
        return []

    tokens: list[str] = []
    for raw_token in re.findall(r"[a-z0-9]+", value.lower()):
        token = raw_token
        if len(token) > 4 and token.endswith("ies"):
            token = f"{token[:-3]}y"
        elif len(token) > 3 and token.endswith("s"):
            token = token[:-1]
        tokens.append(token)
    return tokens


def _significant_subscription_tokens(value: str | None) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for token in _tokenize_subscription_query(value):
        if token in SUBSCRIPTION_QUERY_STOPWORDS or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def search_subscription_feeds(
    db: Session,
    *,
    user_id: int,
    query_text: str,
    limit: int,
):
    """Return visible content rows that match an active subscription name for the user."""
    raw_query_tokens = _tokenize_subscription_query(query_text)
    significant_query_tokens = _significant_subscription_tokens(query_text)
    if not significant_query_tokens:
        return [], None

    query_has_subscription_hint = any(
        token in SUBSCRIPTION_QUERY_HINTS for token in raw_query_tokens
    )
    normalized_query = query_text.strip().lower()
    configs = (
        db.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == user_id)
        .filter(UserScraperConfig.is_active.is_(True))
        .all()
    )

    config_filters = []
    for config in configs:
        candidate_names = [
            (config.display_name or "").strip(),
            str((config.config or {}).get("name") or "").strip(),
        ]
        candidate_names = [name for name in candidate_names if name]
        if not candidate_names:
            continue

        candidate_tokens = set()
        for name in candidate_names:
            candidate_tokens.update(_significant_subscription_tokens(name))
        if not candidate_tokens:
            continue

        name_overlap = set(significant_query_tokens) & candidate_tokens
        if not name_overlap and not any(
            normalized_query
            and (normalized_query in name.lower() or name.lower() in normalized_query)
            for name in candidate_names
        ):
            continue
        if not query_has_subscription_hint and not name_overlap:
            continue

        per_config_filters = [
            or_(
                func.lower(func.coalesce(Content.source, "")).like(f"%{name.lower()}%"),
                func.lower(func.coalesce(Content.title, "")).like(f"%{name.lower()}%"),
                func.lower(func.coalesce(Content.search_text, "")).like(f"%{name.lower()}%"),
            )
            for name in candidate_names
        ]
        token_filters = [
            or_(
                func.lower(func.coalesce(Content.title, "")).like(f"%{token}%"),
                func.lower(func.coalesce(Content.search_text, "")).like(f"%{token}%"),
                func.lower(func.coalesce(Content.source, "")).like(f"%{token}%"),
            )
            for token in sorted(candidate_tokens)
        ]
        if token_filters:
            per_config_filters.append(and_(*token_filters))
        config_filters.append(or_(*per_config_filters))

    if not config_filters:
        return [], None

    matched_query = build_user_feed_query(db, user_id, mode="inbox").filter(or_(*config_filters))
    total_matches = matched_query.order_by(None).count()
    if total_matches == 0:
        return [], 0

    rows = matched_query.order_by(Content.created_at.desc(), Content.id.desc()).limit(limit).all()
    return rows, total_matches
