"""Offline PDFs exercise the real local parser without a crawler/provider call."""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from newsly_document_extractor.models import (
    ExtractIntent,
    ExtractionFailure,
    ExtractionFailureCode,
    ExtractionFallbackRequired,
    ExtractionMethod,
    ExtractionSuccess,
    ExtractOptions,
    ExtractRequest,
    TraceContext,
)
from newsly_document_extractor.pdf import _run_tool
from newsly_document_extractor.policy import ExtractionPolicy
from newsly_document_extractor.settings import ExtractorSettings
from newsly_document_extractor.url_safety import PublicFetch


def pdf_fixture(page_count: int, *, scanned_page: int | None = None) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    image_id = 4 + 2 * page_count
    for number in range(1, page_count + 1):
        page_id = 2 + 2 * number
        if number == scanned_page:
            body = b"q 400 0 0 600 72 72 cm /Scan Do Q"
        else:
            lines = [
                f"Document page {number}. Complete evidence about algorithms and data structures.",
                "Every page is important and must be retained for grounded summaries.",
                "This synthetic document has a searchable text layer and no paid extraction.",
                f"END-OF-PAGE-{number}",
            ]
            body = (
                b"BT /F1 10 Tf 72 720 Td "
                + b" 0 -20 Td ".join(f"({line}) Tj".encode() for line in lines)
                + b" ET"
            )
        objects.extend(
            [
                (
                    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    "/Resources << /Font << /F1 3 0 R >> "
                    + (f"/XObject << /Scan {image_id} 0 R >> " if scanned_page else "")
                    + f">> /Contents {page_id + 1} 0 R >>"
                ).encode(),
                f"<< /Length {len(body)} >>\nstream\n".encode() + body + b"\nendstream",
            ]
        )
    if scanned_page:
        objects.append(
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\n"
            b"stream\n\x00\nendstream"
        )
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(page_count))
    objects[1] = f"<< /Type /Pages /Count {page_count} /Kids [{kids}] >>".encode()
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010} 00000 n \n".encode())
    output.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


class NoBrowser:
    async def crawl(self, **_kwargs: Any) -> Any:
        raise AssertionError("PDFs must not enter the browser/HTML path")

    async def close(self) -> None:
        pass


async def extract(
    body: bytes,
    *,
    media_type: str = "application/pdf",
    limit: int = 1_000_000,
    intent: ExtractIntent = ExtractIntent.EXTRACT_ARTICLE,
) -> Any:
    async def fetcher(_url: str, **kwargs: Any) -> PublicFetch:
        assert kwargs["max_bytes"] == 5_000_000
        assert kwargs["max_redirects"] == 8
        assert 0 < kwargs["timeout_seconds"] <= 30
        return PublicFetch("https://example.com/final.pdf", body, media_type)

    async def public_validator(_url: str) -> tuple[Any, ...]:
        return ()

    policy = ExtractionPolicy(
        ExtractorSettings(environment="test", shared_secret="fixture"),
        crawler=NoBrowser(),
        fetcher=fetcher,
        url_validator=public_validator,
    )
    options = ExtractOptions.defaults().model_copy(update={"max_markdown_bytes": limit})
    return await policy.extract(
        ExtractRequest(
            schema_version=1,
            request_id="offline-pdf",
            url="https://example.com/download",
            intent=intent,
            absolute_deadline=datetime.now(UTC) + timedelta(seconds=30),
            options=options,
            trace=TraceContext(trace_id=None, span_id=None),
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("media_type", ["application/pdf", "application/octet-stream"])
async def test_text_pdf_retains_all_296_pages_without_browser_or_firecrawl(media_type: str) -> None:
    result = await extract(pdf_fixture(296), media_type=media_type)
    assert isinstance(result, ExtractionSuccess)
    assert result.method is ExtractionMethod.STATIC_READABILITY
    assert "## Page 1\n" in result.markdown
    assert "## Page 296\n" in result.markdown
    assert "END-OF-PAGE-296" in result.markdown
    assert len([event for event in result.usage_events if event.kind == "pdf_page"]) == 1
    assert next(event.quantity for event in result.usage_events if event.kind == "pdf_page") == 296
    assert any("all 296 pages retained" in warning for warning in result.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize("pages", [1, 3])
async def test_scanned_or_mixed_pdf_explicitly_requests_ocr_fallback(pages: int) -> None:
    result = await extract(pdf_fixture(pages, scanned_page=pages))
    assert isinstance(result, ExtractionFallbackRequired)
    assert "OCR" in result.reason
    assert result.usage_events[0].kind == "downloaded_body"


@pytest.mark.asyncio
async def test_malformed_pdf_uses_fallback_even_for_static_analysis() -> None:
    result = await extract(b"%PDF-1.4\nbroken", intent=ExtractIntent.STATIC_ANALYZE)
    assert isinstance(result, ExtractionFallbackRequired)
    assert "could not read" in result.reason


@pytest.mark.asyncio
async def test_oversized_full_pdf_text_fails_explicitly_instead_of_truncating_or_spending() -> None:
    result = await extract(pdf_fixture(50), limit=4_096)
    assert isinstance(result, ExtractionFailure)
    assert result.code is ExtractionFailureCode.RESPONSE_TOO_LARGE
    assert "no pages were omitted" in result.message


@pytest.mark.asyncio
async def test_local_pdf_timeout_kills_and_reaps_the_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned = []
    create = asyncio.create_subprocess_exec

    async def tracked_create(*args: Any, **kwargs: Any) -> Any:
        process = await create(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", tracked_create)
    with pytest.raises(TimeoutError):
        await _run_tool(
            sys.executable, "-c", "import time; time.sleep(30)", max_bytes=100, timeout_seconds=0.05
        )
    assert len(spawned) == 1
    assert spawned[0].returncode is not None


@pytest.mark.asyncio
async def test_local_pdf_output_bound_kills_and_drains_a_full_pipe() -> None:
    from newsly_document_extractor.url_safety import UrlSafetyError

    with pytest.raises(UrlSafetyError, match="no pages were omitted"):
        await asyncio.wait_for(
            _run_tool(
                sys.executable,
                "-c",
                "import sys, time; sys.stdout.write('x' * 2000000); "
                "sys.stdout.flush(); time.sleep(30)",
                max_bytes=100,
                timeout_seconds=5,
            ),
            timeout=2,
        )
