from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from newsly_document_extractor.models import (
    SCHEMA_VERSION,
    ExtractIntent,
    ExtractionFallbackRequired,
    ExtractionSuccess,
    ExtractOptions,
    ExtractRequest,
    TraceContext,
)
from newsly_document_extractor.policy import ExtractionPolicy, StaticDocument
from newsly_document_extractor.settings import ExtractorSettings
from newsly_document_extractor.url_safety import UrlSafetyError


class FixtureCrawler:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome

    async def crawl(self, **_kwargs: Any) -> Any:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def close(self) -> None:
        pass


def request(*, browser: bool = True) -> ExtractRequest:
    return ExtractRequest(
        schema_version=SCHEMA_VERSION,
        request_id="local-telemetry",
        url="https://example.com/article",
        intent=ExtractIntent.EXTRACT_ARTICLE,
        absolute_deadline=datetime.now(UTC) + timedelta(seconds=30),
        options=ExtractOptions.defaults().model_copy(update={"allow_browser_fallback": browser}),
        trace=TraceContext(trace_id=None, span_id=None),
    )


def document(*, issue: str | None = None, pages: int | None = None) -> StaticDocument:
    return StaticDocument(
        final_url="https://example.com/article",
        title="Fixture",
        author=None,
        published_at=None,
        markdown="Complete synthetic article reporting. " * 15,
        feed_links=(),
        html="",
        downloaded_bytes=999,
        issue=issue,
        pdf_pages=pages,
    )


def browser_result(*, text: str = "Complete browser body. " * 30) -> Any:
    return SimpleNamespace(
        success=True,
        markdown=SimpleNamespace(raw_markdown=text),
        metadata={"title": "Fixture"},
        html="",
        cleaned_html="",
    )


async def policy_for(static: Any, browser: Any) -> ExtractionPolicy:
    async def allow_public(_url: str) -> tuple[Any, ...]:
        return ()

    policy = ExtractionPolicy(
        ExtractorSettings(environment="test", shared_secret="test-secret"),
        crawler=FixtureCrawler(browser),
        url_validator=allow_public,
    )

    async def fetch(_request: ExtractRequest) -> StaticDocument:
        if isinstance(static, Exception):
            raise static
        return static

    policy._fetch_static = fetch  # type: ignore[method-assign]
    return policy


def failures(result: Any) -> list[str]:
    events = [event for event in result.usage_events if event.kind.startswith("local_failure:")]
    assert all(event.unit == "event" and event.quantity == 1 for event in events)
    return [event.kind for event in events]


@pytest.mark.asyncio
async def test_fallback_reports_both_stages_without_raw_exception_details() -> None:
    policy = await policy_for(
        UrlSafetyError("private-url?token=secret", code="fetch_failed", http_status=403),
        TimeoutError("article text and private-url?token=secret" * 500),
    )
    result = await policy.extract(request())
    assert isinstance(result, ExtractionFallbackRequired)
    assert failures(result) == [
        "local_failure:static_fetch:http_403",
        "local_failure:browser_fetch:timeout",
    ]
    assert result.reason == "local_extraction_failed; static_fetch:http_403; browser_fetch:timeout"
    assert result.retryable
    assert "private-url" not in result.reason
    assert len(result.reason) < 2_000


@pytest.mark.asyncio
async def test_browser_success_retains_static_failure() -> None:
    policy = await policy_for(UrlSafetyError("network", code="fetch_failed"), browser_result())
    result = await policy.extract(request())
    assert isinstance(result, ExtractionSuccess)
    assert failures(result) == ["local_failure:static_fetch:fetch_failed"]
    assert sum(event.quantity for event in result.usage_events if event.kind == "browser_page") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,code",
    [
        (RuntimeError("sensitive exception details"), "browser_fetch:exception"),
        (SimpleNamespace(success=False), "browser_fetch:failed_result"),
        (SimpleNamespace(success=False, status_code=403), "browser_fetch:http_403"),
        (SimpleNamespace(success=False, status_code=401), "browser_fetch:http_401"),
        (browser_result(text=""), "browser_quality:missing_body"),
    ],
)
async def test_static_recovery_retains_browser_failure_without_duplicate_download(
    outcome: Any,
    code: str,
) -> None:
    policy = await policy_for(document(), outcome)
    result = await policy.extract(request())
    assert isinstance(result, ExtractionSuccess)
    assert failures(result) == [f"local_failure:{code}"]
    assert (
        sum(event.quantity for event in result.usage_events if event.kind == "downloaded_body")
        == 999
    )
    assert result.markdown == document().markdown


@pytest.mark.asyncio
async def test_disabled_browser_reports_static_quality_issue() -> None:
    policy = await policy_for(document(issue="paywall"), None)
    result = await policy.extract(request(browser=False))
    assert isinstance(result, ExtractionFallbackRequired)
    assert failures(result) == [
        "local_failure:static_quality:paywall",
        "local_failure:browser_fetch:disabled",
    ]


@pytest.mark.asyncio
async def test_pdf_ocr_failure_is_bounded_and_does_not_attempt_browser() -> None:
    policy = await policy_for(
        document(issue="PDF contains scanned/image pages that require OCR", pages=0), None
    )
    result = await policy.extract(request())
    assert isinstance(result, ExtractionFallbackRequired)
    assert failures(result) == ["local_failure:pdf_extract:ocr_required"]
    assert all(event.kind != "browser_page" for event in result.usage_events)


@pytest.mark.asyncio
async def test_usable_static_article_has_no_failure_telemetry() -> None:
    complete = document()
    complete = replace(complete, markdown=complete.markdown * 3)
    policy = await policy_for(complete, None)
    result = await policy.extract(request())
    assert isinstance(result, ExtractionSuccess)
    assert failures(result) == []
    assert result.markdown == complete.markdown


@pytest.mark.asyncio
async def test_empty_timeout_exception_remains_retryable() -> None:
    policy = await policy_for(document(issue="access_gate_html"), TimeoutError())
    result = await policy.extract(request())
    assert isinstance(result, ExtractionFallbackRequired)
    assert result.retryable
    assert failures(result) == [
        "local_failure:static_quality:access_gate_html",
        "local_failure:browser_fetch:timeout",
    ]
