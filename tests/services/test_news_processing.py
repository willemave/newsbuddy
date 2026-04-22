"""Tests for strict short-form news processing behavior."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from app.models.metadata import ContentType, NewsSummary
from app.models.schema import Content, NewsItem
from app.services import news_article_enrichment as news_article_enrichment_module
from app.services import news_processing as news_processing_module
from app.services.discussion_fetcher import DiscussionFetchResult
from app.services.news_article_bodies import NEWS_ARTICLE_BODY_REF_KEY
from app.services.news_article_enrichment import enrich_news_item_article
from app.services.news_processing import process_news_item


def _require_id(value: int | None) -> int:
    assert value is not None
    return value


def _metadata(value: object | None) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _summarizer(fn: object) -> Any:
    return cast(Any, SimpleNamespace(summarize=fn))


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

    summarizer = _summarizer(lambda *_args, **_kwargs: {"title": "bad payload"})

    result = process_news_item(
        db_session,
        news_item_id=_require_id(item.id),
        summarizer=summarizer,
    )

    db_session.refresh(item)
    assert result.success is False
    assert item.status == "failed"
    assert "invalid payload" in (result.error_message or "")


def test_process_news_item_commits_processing_state_before_summarization(
    db_session_factory,
) -> None:
    write_session = db_session_factory()
    read_session = db_session_factory()
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

        summarizer = _summarizer(_summarize)

        result = process_news_item(
            write_session,
            news_item_id=_require_id(item.id),
            summarizer=summarizer,
        )

        assert result.success is False
        assert observed_statuses == ["processing"]
    finally:
        write_session.close()
        read_session.close()


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

    summarizer = _summarizer(_summarize)

    result = process_news_item(
        db_session,
        news_item_id=_require_id(item.id),
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


def test_process_news_item_finalizes_summary(
    db_session,
) -> None:
    item = NewsItem(
        ingest_key="news-item-finalize-retry",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="125b",
        article_url="https://example.com/story-3b",
        article_title="Example story 3b",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=1253",
        raw_metadata={"excerpt": "Example excerpt"},
        status="pending",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    result = process_news_item(
        db_session,
        news_item_id=_require_id(item.id),
        summarizer=_summarizer(
            lambda *_args, **_kwargs: NewsSummary(
                title="Retry-backed title",
                article_url=item.article_url,
                key_points=["Point one"],
                summary="Short summary.",
            )
        ),
    )

    db_session.refresh(item)
    assert result.success is True
    assert item.status == "ready"


def test_process_news_item_fetches_discussion_via_public_news_item_flow(
    db_session,
    monkeypatch,
) -> None:
    item = NewsItem(
        ingest_key="news-item-discussion-flow",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="discussion-flow-1",
        article_url="https://example.com/story-discussion-flow",
        article_title="Story with discussion",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=777",
        raw_metadata={"excerpt": "Example excerpt"},
        status="pending",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    captured: dict[str, object] = {}

    def _fetch_and_store(db, *, news_item_id: int, comment_cap: int):
        assert news_item_id == item.id
        assert comment_cap > 0
        persisted_item = db.get(NewsItem, news_item_id)
        assert persisted_item is not None
        metadata = dict(persisted_item.raw_metadata or {})
        metadata["discussion_payload"] = {
            "mode": "comments",
            "source_url": persisted_item.discussion_url,
            "comments": [
                {
                    "comment_id": "c1",
                    "author": "alice",
                    "text": "This changed the market.",
                    "compact_text": "This changed the market.",
                    "depth": 0,
                }
            ],
            "compact_comments": ["This changed the market."],
            "discussion_groups": [],
            "links": [],
            "stats": {"declared_comment_count": 1},
        }
        metadata["discussion_status"] = "completed"
        persisted_item.raw_metadata = metadata
        db.commit()
        return DiscussionFetchResult(success=True, status="completed", retryable=False)

    def _summarize(prompt: str, **_kwargs):
        captured["prompt"] = prompt
        return NewsSummary(
            title="Fresh digest title",
            article_url=item.article_url,
            key_points=["Fresh point"],
            summary="Fresh summary text.",
        )

    monkeypatch.setattr(
        news_processing_module,
        "fetch_and_store_news_item_discussion",
        _fetch_and_store,
    )

    result = process_news_item(
        db_session,
        news_item_id=_require_id(item.id),
        summarizer=_summarizer(_summarize),
    )

    db_session.refresh(item)
    assert result.success is True
    prompt = cast(str, captured["prompt"])
    assert "Discussion snippets:" in prompt
    assert "This changed the market." in prompt
    assert _metadata(item.raw_metadata)["discussion_status"] == "completed"
    assert item.status == "ready"


def test_process_news_item_continues_when_discussion_fetch_fails(
    db_session,
    monkeypatch,
) -> None:
    item = NewsItem(
        ingest_key="news-item-discussion-failure",
        visibility_scope="global",
        platform="reddit",
        source_type="reddit",
        source_label="Reddit",
        source_external_id="discussion-failure-1",
        article_url="https://example.com/story-discussion-failure",
        article_title="Story without discussion",
        article_domain="example.com",
        discussion_url="https://reddit.com/r/example/comments/fail/story/",
        raw_metadata={"excerpt": "Source excerpt survives."},
        status="pending",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    def _fetch_and_store(db, *, news_item_id: int, comment_cap: int):
        persisted_item = db.get(NewsItem, news_item_id)
        assert persisted_item is not None
        metadata = dict(persisted_item.raw_metadata or {})
        metadata["discussion_payload"] = {
            "mode": "none",
            "source_url": persisted_item.discussion_url,
            "comments": [],
            "compact_comments": [],
            "discussion_groups": [],
            "links": [],
            "stats": {},
        }
        metadata["discussion_status"] = "failed"
        metadata["discussion_error"] = "Discussion fetch failed: blocked"
        persisted_item.raw_metadata = metadata
        db.commit()
        return DiscussionFetchResult(
            success=False,
            status="failed",
            error_message="Discussion fetch failed: blocked",
            retryable=False,
        )

    monkeypatch.setattr(
        news_processing_module,
        "fetch_and_store_news_item_discussion",
        _fetch_and_store,
    )

    result = process_news_item(
        db_session,
        news_item_id=_require_id(item.id),
        summarizer=_summarizer(
            lambda *_args, **_kwargs: NewsSummary(
                title="Recovered title",
                article_url=item.article_url,
                key_points=["Recovered point"],
                summary="Recovered summary.",
            )
        ),
    )

    db_session.refresh(item)
    assert result.success is True
    assert item.status == "ready"
    item_metadata = _metadata(item.raw_metadata)
    assert item_metadata["discussion_status"] == "failed"
    assert item_metadata["discussion_error"] == "Discussion fetch failed: blocked"


def test_process_news_item_marks_failure_on_invalid_summary_payload(
    db_session,
) -> None:
    item = NewsItem(
        ingest_key="news-item-failure-retry",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="discussion-failure-2",
        article_url="https://example.com/story-discussion-failure-2",
        article_title="Story with invalid summary output",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=778",
        raw_metadata={"excerpt": "Example excerpt"},
        status="pending",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    result = process_news_item(
        db_session,
        news_item_id=_require_id(item.id),
        summarizer=_summarizer(lambda *_args, **_kwargs: {"bad": "payload"}),
    )

    db_session.refresh(item)
    assert result.success is False
    assert item.status == "failed"


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

    summarizer = _summarizer(_summarize)

    result = process_news_item(
        db_session,
        news_item_id=_require_id(item.id),
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

    summarizer = _summarizer(_summarize)

    result = process_news_item(
        db_session,
        news_item_id=_require_id(item.id),
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
    item_metadata = _metadata(item.raw_metadata)
    assert item_metadata["summary_kind"] == "short_news"
    assert item_metadata["summary_version"] == 1


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
            "summary_kind": "short_news",
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

    summarizer = _summarizer(_summarize)

    result = process_news_item(
        db_session,
        news_item_id=_require_id(item.id),
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

    summarizer = _summarizer(_summarize)

    result = process_news_item(
        db_session,
        news_item_id=_require_id(item.id),
        summarizer=summarizer,
    )

    db_session.refresh(item)
    assert result.success is True
    assert result.generated_summary is True
    captured_kwargs = cast(dict[str, Any], captured["kwargs"])
    captured_args = cast(tuple[Any, ...], captured["args"])
    assert captured_kwargs["title"] is None
    assert "Article title: VOID" not in captured_args[0]
    assert item.summary_title == "Fresh digest title"
    assert item.status == "ready"


def test_process_news_item_rewrites_placeholder_generated_title_from_summary_text(
    db_session,
) -> None:
    item = NewsItem(
        ingest_key="news-item-skill-title",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="skill-title-1",
        article_url="https://example.com/story-skill-title",
        article_title="SKILL0",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=skill-title-1",
        raw_metadata={
            "excerpt": "A concise source excerpt.",
            "aggregator": {"title": "SKILL0", "author": "alice"},
            "discussion_payload": {"comments": []},
        },
        status="new",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    result = process_news_item(
        db_session,
        news_item_id=_require_id(item.id),
        summarizer=_summarizer(
            lambda prompt, **_kwargs: NewsSummary(
                title="SKILL0",
                article_url=item.article_url,
                key_points=["One concrete point."],
                summary=(
                    "A Hugging Face space demo that explains how a tiny skill model "
                    "works in practice."
                ),
            )
        ),
    )

    db_session.refresh(item)
    assert result.success is True
    assert item.summary_title == (
        "A Hugging Face space demo that explains how a tiny skill model works in practice."
    )
    assert _metadata(_metadata(item.raw_metadata)["summary"])["title"] == item.summary_title


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

    summarizer = _summarizer(
        lambda *_args, **_kwargs: NewsSummary(
            title=long_title,
            article_url=item.article_url,
            key_points=["Fresh point"],
            summary="Fresh summary text.",
        )
    )

    result = process_news_item(
        db_session,
        news_item_id=_require_id(item.id),
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
            "summary_kind": "short_news",
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
        news_item_id=_require_id(item.id),
        summarizer=_summarizer(_summarize),
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
        news_item_id=_require_id(item.id),
        summarizer=_summarizer(_summarize),
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

    result = enrich_news_item_article(db_session, news_item_id=_require_id(item.id))

    db_session.refresh(item)
    assert result.success is True
    assert result.source == "content"
    item_metadata = _metadata(item.raw_metadata)
    body_ref = _metadata(item_metadata[NEWS_ARTICLE_BODY_REF_KEY])
    extraction = _metadata(item_metadata["article_extraction"])
    assert body_ref["kind"] == "content"
    assert body_ref["content_id"] == article.id
    assert extraction["status"] == "completed"


def test_enrich_news_item_article_uses_stored_tweet_metadata_without_x_refetch(
    db_session,
    monkeypatch,
) -> None:
    item = NewsItem(
        ingest_key="news-item-tweet-metadata-reuse",
        visibility_scope="user",
        owner_user_id=1,
        platform="twitter",
        source_type="x_timeline",
        source_label="X Following",
        source_external_id="123",
        article_url="https://x.com/i/status/123",
        canonical_story_url="https://x.com/i/status/123",
        canonical_item_url="https://x.com/i/status/123",
        discussion_url="https://x.com/i/status/123",
        article_title="Native digest title",
        raw_metadata={
            "tweet_article_title": "Native digest title",
            "tweet_article_text": "Full native digest body.",
            "tweet_text": "Teaser text",
        },
        status="new",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    def _unexpected_strategy_registry():
        raise AssertionError("strategy registry should not be used when tweet metadata is present")

    monkeypatch.setattr(
        news_article_enrichment_module,
        "get_strategy_registry",
        _unexpected_strategy_registry,
    )

    result = enrich_news_item_article(db_session, news_item_id=_require_id(item.id))

    db_session.refresh(item)
    assert result.success is True
    assert result.source == "metadata"
    item_metadata = _metadata(item.raw_metadata)
    body_ref = _metadata(item_metadata[NEWS_ARTICLE_BODY_REF_KEY])
    extraction = _metadata(item_metadata["article_extraction"])
    assert body_ref["kind"] == "inline"
    assert "Full native digest body." in body_ref["text"]
    assert extraction["status"] == "completed"
    assert extraction["source"] == "metadata"


def test_enrich_news_item_article_passes_context_to_strategy(db_session) -> None:
    item = NewsItem(
        ingest_key="news-item-strategy-context",
        visibility_scope="user",
        owner_user_id=1,
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="strategy-context-1",
        article_url="https://example.com/story-10",
        canonical_story_url="https://example.com/story-10",
        article_title="Example story 10",
        raw_metadata={"rss_content": "<p>Recovered RSS content for enrichment.</p>"},
        status="new",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    class _FakeStrategy:
        def __init__(self) -> None:
            self.last_context: dict[str, Any] | None = None

        def preprocess_url(self, url: str) -> str:
            return url

        def download_content(self, url: str) -> str:
            return f"<html>{url}</html>"

        def extract_data(
            self,
            _content: str,
            url: str,
            context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            self.last_context = dict(context or {})
            return {
                "title": "Recovered article title",
                "text_content": "Recovered RSS content for enrichment.",
                "content_type": "html",
                "source": "example.com",
                "final_url_after_redirects": url,
                "gate_page_reason": "access gate detected: challenge/JS wall content",
                "used_rss_fallback": True,
                "extraction_error": None,
            }

        def prepare_for_llm(self, extracted_data: dict[str, Any]) -> dict[str, Any]:
            return {"content_to_summarize": extracted_data["text_content"]}

    class _FakeRegistry:
        def __init__(self, strategy: _FakeStrategy) -> None:
            self._strategy = strategy

        def get_strategy(self, url: str) -> _FakeStrategy:
            assert url == "https://example.com/story-10"
            return self._strategy

    strategy = _FakeStrategy()

    result = enrich_news_item_article(
        db_session,
        news_item_id=_require_id(item.id),
        strategy_registry=_FakeRegistry(strategy),  # type: ignore[arg-type]
    )

    db_session.refresh(item)
    assert result.success is True
    assert result.status == "completed"
    assert strategy.last_context is not None
    assert strategy.last_context["content_id"] == item.id
    assert strategy.last_context["existing_metadata"]["rss_content"] == (
        "<p>Recovered RSS content for enrichment.</p>"
    )
    extraction = _metadata(_metadata(item.raw_metadata)["article_extraction"])
    assert extraction["status"] == "completed"


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
        news_item_id=_require_id(item.id),
        summarizer=_summarizer(_summarize),
    )

    db_session.refresh(item)
    assert result.success is True
    prompt = str(captured["prompt"])
    assert "Article body:" in prompt
    assert "Full extracted article body for prompt grounding." in prompt
    assert "Excerpt:" in prompt
    assert item.summary_text == "Body-informed summary."
