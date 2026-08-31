import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from newsly_document_extractor.models import (
    SCHEMA_VERSION,
    ExtractIntent,
    ExtractionFailure,
    ExtractionFailureCode,
    ExtractOptions,
    ExtractRequest,
    TraceContext,
)
from newsly_document_extractor.policy import (
    ExtractionPolicy,
    _clean_markdown,
    _detect_issue,
    _extract_pubmed_link,
    parse_static_document,
)
from newsly_document_extractor.settings import ExtractorSettings
from newsly_document_extractor.url_safety import PublicFetch


class UnusedCrawler:
    async def crawl(self, **_kwargs: Any) -> Any:
        raise AssertionError("static analysis must not open a browser")

    async def close(self) -> None:
        return None


def test_static_article_policy_extracts_metadata_and_feed() -> None:
    html = b"""
    <html>
      <head>
        <title>Fixture Article</title>
        <meta name="author" content="Fixture Author">
        <link rel="alternate" type="application/rss+xml" href="/feed.xml">
      </head>
      <body><article><h1>Fixture Article</h1><p>A synthetic paragraph.</p></article></body>
    </html>
    """

    document = parse_static_document(
        PublicFetch(
            final_url="https://example.com/article",
            body=html,
            content_type="text/html; charset=utf-8",
        )
    )

    assert document.title == "Fixture Article"
    assert document.author == "Fixture Author"
    assert "A synthetic paragraph." in document.markdown
    assert document.feed_links == ("https://example.com/feed.xml",)
    assert document.issue is None


def test_pubmed_policy_prefers_pmc_full_text() -> None:
    html = """
    <aside id="full-text-links">
      <a href="https://publisher.example/article">Publisher</a>
      <a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC123/">PMC</a>
    </aside>
    """

    assert (
        _extract_pubmed_link(html, "https://pubmed.ncbi.nlm.nih.gov/123/")
        == "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"
    )


def test_reuters_cleanup_keeps_article_and_removes_known_chrome() -> None:
    markdown = (
        "Reuters navigation NEW YORK, Aug 30 (Reuters) - Article body. "
        "Advertisement · Scroll to continue More reporting. Reporting by Fixture Author"
    )

    cleaned = _clean_markdown("https://www.reuters.com/world/fixture", markdown)

    assert cleaned.startswith("NEW YORK, Aug 30 (Reuters) - Article body.")
    assert "Advertisement" not in cleaned
    assert "Reporting by" not in cleaned


def test_discussion_only_result_is_rejected_for_an_article_url() -> None:
    issue = _detect_issue(
        url="https://example.com/article",
        title="Fixture article",
        markdown="Discussion about this post Comments restacks",
        html=None,
    )

    assert issue == "discussion_only"


def test_static_readability_wins_over_chrome_heavy_browser_markdown() -> None:
    assert ExtractionPolicy._prefer_static_over_browser(
        "# Article\n\nA clean article body with useful reporting.",
        "Skip to main content [One](/one) [Two](/two) [Three](/three)",
    )


@pytest.mark.asyncio
async def test_extraction_deadline_includes_concurrency_admission() -> None:
    first_fetch_started = asyncio.Event()
    release_first_fetch = asyncio.Event()

    async def blocked_fetcher(_url: str, **_kwargs: Any) -> PublicFetch:
        first_fetch_started.set()
        await release_first_fetch.wait()
        return PublicFetch(
            final_url="https://example.com/article",
            body=b"<html><title>Fixture</title><article>Readable body</article></html>",
            content_type="text/html",
        )

    async def allow_public(_url: str) -> tuple[Any, ...]:
        return ()

    policy = ExtractionPolicy(
        ExtractorSettings(
            environment="test",
            shared_secret="test-secret",
            max_concurrent_extractions=1,
        ),
        crawler=UnusedCrawler(),
        fetcher=blocked_fetcher,
        url_validator=allow_public,
    )

    def request(request_id: str, deadline_ms: int) -> ExtractRequest:
        return ExtractRequest(
            schema_version=SCHEMA_VERSION,
            request_id=request_id,
            url="https://example.com/article",
            intent=ExtractIntent.STATIC_ANALYZE,
            absolute_deadline=datetime.now(UTC) + timedelta(milliseconds=deadline_ms),
            options=ExtractOptions.defaults(),
            trace=TraceContext(trace_id=None, span_id=None),
        )

    first_task = asyncio.create_task(policy.extract(request("first", 1_000)))
    await first_fetch_started.wait()
    try:
        queued_result = await asyncio.wait_for(policy.extract(request("queued", 20)), timeout=0.5)
    finally:
        release_first_fetch.set()
    await first_task
    await policy.close()

    assert isinstance(queued_result, ExtractionFailure)
    assert queued_result.code is ExtractionFailureCode.DEADLINE_EXCEEDED
