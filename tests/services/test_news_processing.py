"""Tests for strict short-form news processing behavior."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.metadata import NewsSummary
from app.models.schema import Base, NewsItem
from app.services.news_processing import process_news_item


def test_process_news_item_fails_on_invalid_summarizer_output(db_session) -> None:
    item = NewsItem(
        ingest_key="news-item-invalid-summary",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="123",
        article_url="https://example.com/story",
        article_title="Example story",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=123",
        raw_metadata={"excerpt": "Example excerpt"},
        status="pending",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    summarizer = SimpleNamespace(summarize=lambda *_args, **_kwargs: {"title": "bad payload"})

    result = process_news_item(
        db_session,
        news_item_id=item.id,
        summarizer=summarizer,
    )

    db_session.refresh(item)
    assert result.success is False
    assert item.status == "failed"
    assert "invalid payload" in (result.error_message or "")


def test_process_news_item_commits_processing_state_before_summarization(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'news-processing.db'}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    write_session = session_factory()
    read_session = session_factory()
    try:
        item = NewsItem(
            ingest_key="news-item-processing-commit",
            visibility_scope="global",
            platform="hackernews",
            source_type="hackernews",
            source_label="Hacker News",
            source_external_id="124",
            article_url="https://example.com/story-2",
            article_title="Example story 2",
            article_domain="example.com",
            discussion_url="https://news.ycombinator.com/item?id=124",
            raw_metadata={"excerpt": "Example excerpt"},
            status="pending",
        )
        write_session.add(item)
        write_session.commit()
        write_session.refresh(item)

        observed_statuses: list[str | None] = []

        def _summarize(*_args, **_kwargs):
            read_session.expire_all()
            observed = read_session.get(NewsItem, item.id)
            observed_statuses.append(observed.status if observed is not None else None)
            return {"title": "bad payload"}

        summarizer = SimpleNamespace(summarize=_summarize)

        result = process_news_item(
            write_session,
            news_item_id=item.id,
            summarizer=summarizer,
        )

        assert result.success is False
        assert observed_statuses == ["processing"]
    finally:
        write_session.close()
        read_session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_process_news_item_passes_usage_persistence_context(db_session) -> None:
    item = NewsItem(
        ingest_key="news-item-usage-context",
        visibility_scope="global",
        owner_user_id=77,
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="125",
        article_url="https://example.com/story-3",
        article_title="Example story 3",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=125",
        raw_metadata={"excerpt": "Example excerpt"},
        status="pending",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    captured: dict[str, object] = {}

    def _summarize(*_args, **kwargs):
        captured.update(kwargs)
        return NewsSummary(
            title="Compact title",
            article_url=item.article_url,
            key_points=["Point one"],
            summary="Short summary.",
        )

    summarizer = SimpleNamespace(summarize=_summarize)

    result = process_news_item(
        db_session,
        news_item_id=item.id,
        summarizer=summarizer,
    )

    assert result.success is True
    assert captured["db"] is db_session
    assert captured["usage_persist"] == {
        "feature": "news_processing",
        "operation": "news_processing.summarize_short_form",
        "source": "queue",
        "user_id": 77,
        "metadata": {
            "news_item_id": item.id,
            "source_type": "hackernews",
        },
    }
