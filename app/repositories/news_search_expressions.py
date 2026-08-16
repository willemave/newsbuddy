from __future__ import annotations

from typing import Literal

from sqlalchemy import String, func, literal_column
from sqlalchemy.sql.elements import ColumnElement

from app.models.db import NewsItem


def news_summary_title_expression() -> ColumnElement[str]:
    return func.coalesce(
        NewsItem.raw_metadata.op("->")(literal_column("'summary'")).op("->>", return_type=String)(
            literal_column("'title'")
        ),
        literal_column("''"),
    )


def news_article_title_expression() -> ColumnElement[str]:
    return func.coalesce(
        NewsItem.raw_metadata.op("->")(literal_column("'article'")).op("->>", return_type=String)(
            literal_column("'title'")
        ),
        literal_column("''"),
    )


def news_cluster_titles_expression() -> ColumnElement[str]:
    return func.coalesce(
        NewsItem.raw_metadata.op("->")(literal_column("'cluster'")).op("->>", return_type=String)(
            literal_column("'related_titles'")
        ),
        literal_column("''"),
    )


def news_summary_text_expression() -> ColumnElement[str]:
    return func.coalesce(NewsItem.summary_text, literal_column("''"))


def news_source_label_expression() -> ColumnElement[str]:
    return func.coalesce(NewsItem.source_label, literal_column("''"))


def news_article_domain_expression() -> ColumnElement[str]:
    return func.coalesce(NewsItem.article_domain, literal_column("''"))


def news_provenance_text_expression() -> ColumnElement[str]:
    return (
        news_source_label_expression()
        + literal_column("' '")
        + news_article_domain_expression()
        + literal_column("' '")
        + news_cluster_titles_expression()
    )


def news_search_document_expression() -> ColumnElement[str]:
    """Return the weighted document backed by the canonical News search GIN index."""

    return (
        _weighted_vector(news_summary_title_expression(), "A")
        .op("||")(_weighted_vector(news_article_title_expression(), "B"))
        .op("||")(_weighted_vector(news_summary_text_expression(), "C"))
        .op("||")(_weighted_vector(news_provenance_text_expression(), "D"))
    )


def news_relation_title_document_expression() -> ColumnElement[str]:
    """Return the title-only document used for relation-candidate retrieval."""

    return (
        _weighted_vector(news_summary_title_expression(), "A")
        .op("||")(_weighted_vector(news_article_title_expression(), "A"))
        .op("||")(_weighted_vector(news_cluster_titles_expression(), "B"))
    )


def _weighted_vector(
    text_expression: ColumnElement[str],
    weight: Literal["A", "B", "C", "D"],
) -> ColumnElement[str]:
    weight_literal = {"A": "'A'", "B": "'B'", "C": "'C'", "D": "'D'"}[weight]
    return func.setweight(
        func.to_tsvector(literal_column("'english'"), text_expression),
        literal_column(weight_literal),
    )
