"""Canonical PostgreSQL search expressions for long-form content."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import String, func, literal_column, or_
from sqlalchemy.sql.elements import ColumnElement

from app.models.db import Content

CONTENT_SEARCH_DOCUMENT_SQL = """(
    setweight(to_tsvector('english', COALESCE(content_metadata -> 'summary' ->> 'title', '')), 'A')
    || setweight(to_tsvector('english', COALESCE(title, '')), 'B')
    || setweight(to_tsvector('english', COALESCE(source, '')), 'C')
    || setweight(to_tsvector('english', COALESCE(search_text, '')), 'D')
)"""


@dataclass(frozen=True)
class ContentSearchExpressions:
    """PostgreSQL filter and rank expressions for one normalized query."""

    matches: ColumnElement[bool]
    rank: ColumnElement[float]
    tsquery: ColumnElement[str]


def content_summary_title_expression() -> ColumnElement[str]:
    return func.coalesce(
        Content.content_metadata.op("->")(literal_column("'summary'")).op(
            "->>", return_type=String
        )(literal_column("'title'")),
        literal_column("''"),
    )


def content_title_expression() -> ColumnElement[str]:
    return func.coalesce(Content.title, literal_column("''"))


def content_source_expression() -> ColumnElement[str]:
    return func.coalesce(Content.source, literal_column("''"))


def content_search_text_expression() -> ColumnElement[str]:
    return func.coalesce(Content.search_text, literal_column("''"))


def content_search_document_expression() -> ColumnElement[str]:
    """Return the weighted document backed by the canonical content GIN index."""

    return literal_column(CONTENT_SEARCH_DOCUMENT_SQL)


def content_search_expressions(query_text: str) -> ContentSearchExpressions:
    """Build the shared PostgreSQL match and ranking expressions."""

    normalized = " ".join(query_text.split()).strip()
    tsquery = func.websearch_to_tsquery("english", normalized)
    document = content_search_document_expression()
    fts_rank = func.ts_rank_cd(document, tsquery)
    summary_title_match = content_summary_title_expression().bool_op("OPERATOR(public.%>>)")(
        normalized
    )
    title_match = Content.title.bool_op("OPERATOR(public.%>>)")(normalized)
    source_match = Content.source.bool_op("OPERATOR(public.%>>)")(normalized)
    trigram_rank = func.greatest(
        func.public.word_similarity(normalized, content_summary_title_expression()),
        func.public.word_similarity(normalized, content_title_expression()),
        func.public.word_similarity(normalized, content_source_expression()),
    )
    return ContentSearchExpressions(
        matches=or_(
            document.op("@@")(tsquery),
            summary_title_match,
            title_match,
            source_match,
        ),
        rank=func.greatest(fts_rank, trigram_rank * 0.25),
        tsquery=tsquery,
    )
