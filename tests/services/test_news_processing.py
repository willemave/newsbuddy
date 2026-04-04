"""Tests for strict short-form news processing behavior."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.metadata import ContentType, NewsSummary
from app.models.schema import Base, Content, NewsItem
from app.services import news_processing as news_processing_module
from app.services.news_article_bodies import NEWS_ARTICLE_BODY_REF_KEY
from app.services.news_article_enrichment import enrich_news_item_article
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


def test_process_news_item_does_not_treat_title_only_row_as_summarized(db_session) -> None:
    item = NewsItem(
        ingest_key="news-item-title-only",
        visibility_scope="global",
        platform="reddit",
        source_type="reddit",
        source_label="Reddit",
        source_external_id="title-only-1",
        article_url="https://example.com/story-4",
        article_title="Example story 4",
        article_domain="example.com",
        discussion_url="https://reddit.com/r/example/comments/title_only/example_story_4/",
        summary_title="Example story 4",
        raw_metadata={"excerpt": "Useful source excerpt for summarization."},
        status="new",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    calls: list[dict[str, object]] = []

    def _summarize(*_args, **kwargs):
        calls.append(kwargs)
        return NewsSummary(
            title="Example story 4",
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

    db_session.refresh(item)
    assert result.success is True
    assert result.used_existing_summary is False
    assert result.generated_summary is True
    assert len(calls) == 1
    assert item.status == "ready"
    assert item.summary_key_points == ["Point one"]
    assert item.summary_text == "Short summary."


def test_process_news_item_regenerates_legacy_prefilled_summary(db_session) -> None:
    item = NewsItem(
        ingest_key="news-item-legacy-summary",
        visibility_scope="global",
        platform="reddit",
        source_type="reddit",
        source_label="Reddit",
        source_external_id="legacy-summary-1",
        article_url="https://example.com/story-5",
        article_title="Example story 5",
        article_domain="example.com",
        discussion_url="https://reddit.com/r/example/comments/legacy/example_story_5/",
        summary_title="Legacy summary title",
        summary_key_points=["Legacy point"],
        summary_text="Legacy summary text.",
        raw_metadata={
            "excerpt": "Useful source excerpt for regeneration.",
            "summary": {
                "title": "Legacy summary title",
                "key_points": ["Legacy point"],
                "summary": "Legacy summary text.",
            },
        },
        status="ready",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    calls: list[dict[str, object]] = []

    def _summarize(*_args, **kwargs):
        calls.append(kwargs)
        return NewsSummary(
            title="Fresh digest title",
            article_url=item.article_url,
            key_points=["Fresh point"],
            summary="Fresh summary text.",
        )

    summarizer = SimpleNamespace(summarize=_summarize)

    result = process_news_item(
        db_session,
        news_item_id=item.id,
        summarizer=summarizer,
    )

    db_session.refresh(item)
    assert result.success is True
    assert result.used_existing_summary is False
    assert result.generated_summary is True
    assert len(calls) == 1
    assert item.summary_title == "Fresh digest title"
    assert item.summary_key_points == ["Fresh point"]
    assert item.summary_text == "Fresh summary text."
    assert item.raw_metadata["summary_kind"] == "short_news_digest"
    assert item.raw_metadata["summary_version"] == 1


def test_process_news_item_reuses_generated_short_digest(db_session) -> None:
    item = NewsItem(
        ingest_key="news-item-generated-summary",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="generated-summary-1",
        article_url="https://example.com/story-6",
        article_title="Example story 6",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=126",
        summary_title="Generated digest title",
        summary_key_points=["Generated point"],
        summary_text="Generated summary text.",
        raw_metadata={
            "summary": {
                "title": "Generated digest title",
                "article_url": "https://example.com/story-6",
                "key_points": ["Generated point"],
                "summary": "Generated summary text.",
            },
            "summary_kind": "short_news_digest",
            "summary_version": 1,
        },
        status="ready",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    calls = 0

    def _summarize(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return NewsSummary(
            title="Should not be used",
            article_url=item.article_url,
            key_points=["Unexpected"],
            summary="Unexpected",
        )

    summarizer = SimpleNamespace(summarize=_summarize)

    result = process_news_item(
        db_session,
        news_item_id=item.id,
        summarizer=summarizer,
    )

    db_session.refresh(item)
    assert result.success is True
    assert result.used_existing_summary is True
    assert result.generated_summary is False
    assert calls == 0
    assert item.summary_title == "Generated digest title"
    assert item.summary_key_points == ["Generated point"]
    assert item.summary_text == "Generated summary text."


def test_process_news_item_ignores_void_placeholder_titles(db_session) -> None:
    item = NewsItem(
        ingest_key="news-item-void-title",
        visibility_scope="global",
        platform="reddit",
        source_type="reddit",
        source_label="Reddit",
        source_external_id="void-title-1",
        article_url="https://example.com/story-7",
        article_title="VOID",
        article_domain="example.com",
        discussion_url="https://reddit.com/r/example/comments/void/example_story_7/",
        summary_title="VOID",
        raw_metadata={
            "excerpt": "Useful source excerpt for summarization.",
            "discussion_payload": {"comments": []},
        },
        status="new",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    captured: dict[str, object] = {}

    def _summarize(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return NewsSummary(
            title="Fresh digest title",
            article_url=item.article_url,
            key_points=["Fresh point"],
            summary="Fresh summary text.",
        )

    summarizer = SimpleNamespace(summarize=_summarize)

    result = process_news_item(
        db_session,
        news_item_id=item.id,
        summarizer=summarizer,
    )

    db_session.refresh(item)
    assert result.success is True
    assert result.generated_summary is True
    assert captured["kwargs"]["title"] is None
    assert "Article title: VOID" not in captured["args"][0]
    assert item.summary_title == "Fresh digest title"
    assert item.status == "ready"


def test_process_news_item_accepts_long_generated_titles(db_session) -> None:
    long_title = "A" * 400
    item = NewsItem(
        ingest_key="news-item-long-generated-title",
        visibility_scope="global",
        platform="reddit",
        source_type="reddit",
        source_label="Reddit",
        source_external_id="long-generated-title-1",
        article_url="https://example.com/story-7b",
        article_title="Example story 7b",
        article_domain="example.com",
        discussion_url="https://reddit.com/r/example/comments/long/example_story_7b/",
        raw_metadata={
            "excerpt": "Useful source excerpt for summarization.",
            "discussion_payload": {"comments": []},
        },
        status="new",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    summarizer = SimpleNamespace(
        summarize=lambda *_args, **_kwargs: NewsSummary(
            title=long_title,
            article_url=item.article_url,
            key_points=["Fresh point"],
            summary="Fresh summary text.",
        )
    )

    result = process_news_item(
        db_session,
        news_item_id=item.id,
        summarizer=summarizer,
    )

    db_session.refresh(item)
    assert result.success is True
    assert item.summary_title == long_title
    assert item.status == "ready"


def test_process_news_item_reuses_generated_digest_with_long_title(db_session) -> None:
    long_title = ("Signal " * 60).strip()
    item = NewsItem(
        ingest_key="news-item-existing-long-title",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="existing-long-title-1",
        article_url="https://example.com/story-6b",
        article_title="Example story 6b",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=1266",
        summary_title=long_title,
        summary_key_points=["Generated point"],
        summary_text="Generated summary text.",
        raw_metadata={
            "summary": {
                "title": long_title,
                "article_url": "https://example.com/story-6b",
                "key_points": ["Generated point"],
                "summary": "Generated summary text.",
            },
            "summary_kind": "short_news_digest",
            "summary_version": 1,
        },
        status="ready",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    calls = 0

    def _summarize(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return NewsSummary(
            title="Should not be used",
            article_url=item.article_url,
            key_points=["Unexpected"],
            summary="Unexpected",
        )

    result = process_news_item(
        db_session,
        news_item_id=item.id,
        summarizer=SimpleNamespace(summarize=_summarize),
    )

    db_session.refresh(item)
    assert result.success is True
    assert result.used_existing_summary is True
    assert result.generated_summary is False
    assert calls == 0
    assert item.summary_title == long_title


def test_process_news_item_preserves_short_valid_titles(db_session) -> None:
    item = NewsItem(
        ingest_key="news-item-short-title",
        visibility_scope="global",
        platform="twitter",
        source_type="twitter",
        source_label="X",
        source_external_id="short-title-1",
        article_url="https://x.com/i/status/short-title-1",
        canonical_story_url="https://x.com/i/status/short-title-1",
        article_title="xAI",
        article_domain="x.com",
        discussion_url="https://x.com/i/status/short-title-1",
        raw_metadata={"excerpt": "Short title should survive normalization."},
        status="new",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    def _summarize(*args, **kwargs):  # noqa: ANN002, ANN003
        return NewsSummary(
            title="xAI",
            article_url=item.article_url,
            key_points=["Concrete short-title point."],
            summary="Short valid titles should not be discarded.",
        )

    result = process_news_item(
        db_session,
        news_item_id=item.id,
        summarizer=SimpleNamespace(summarize=_summarize),
    )

    db_session.refresh(item)
    assert result.success is True
    assert item.summary_title == "xAI"


def test_enrich_news_item_article_reuses_existing_article_content(db_session) -> None:
    article = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/story-8",
        source_url="https://example.com/story-8",
        title="Existing article body",
        source="example.com",
        platform=None,
        is_aggregate=False,
        status="completed",
        content_metadata={"content_to_summarize": "Full extracted article body."},
    )
    db_session.add(article)
    db_session.flush()

    item = NewsItem(
        ingest_key="news-item-content-reuse",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="content-reuse-1",
        article_url="https://example.com/story-8",
        canonical_story_url="https://example.com/story-8",
        article_title="Example story 8",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=128",
        raw_metadata={"excerpt": "Short excerpt."},
        status="new",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    result = enrich_news_item_article(db_session, news_item_id=item.id)

    db_session.refresh(item)
    assert result.success is True
    assert result.source == "content"
    assert item.raw_metadata[NEWS_ARTICLE_BODY_REF_KEY]["kind"] == "content"
    assert item.raw_metadata[NEWS_ARTICLE_BODY_REF_KEY]["content_id"] == article.id
    assert item.raw_metadata["article_extraction"]["status"] == "completed"


def test_process_news_item_includes_resolved_article_body_in_prompt(
    db_session,
    monkeypatch,
) -> None:
    item = NewsItem(
        ingest_key="news-item-article-body-prompt",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="article-body-prompt-1",
        article_url="https://example.com/story-9",
        article_title="Example story 9",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=129",
        raw_metadata={"excerpt": "Fallback excerpt."},
        status="new",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    class _Resolver:
        def resolve_text(self, db, *, news_item):
            assert db is db_session
            assert news_item.id == item.id
            return "Full extracted article body for prompt grounding."

    monkeypatch.setattr(
        news_processing_module,
        "get_news_item_article_body_resolver",
        lambda: _Resolver(),
    )

    captured: dict[str, object] = {}

    def _summarize(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return NewsSummary(
            title="Prompt-grounded title",
            article_url=item.article_url,
            key_points=["Body-informed point"],
            summary="Body-informed summary.",
        )

    result = process_news_item(
        db_session,
        news_item_id=item.id,
        summarizer=SimpleNamespace(summarize=_summarize),
    )

    db_session.refresh(item)
    assert result.success is True
    prompt = str(captured["prompt"])
    assert "Article body:" in prompt
    assert "Full extracted article body for prompt grounding." in prompt
    assert "Excerpt:" in prompt
    assert item.summary_text == "Body-informed summary."
