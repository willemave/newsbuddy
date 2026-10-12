"""Bounded local Poppler extraction; no OCR service or page-range truncation."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit

from newsly_document_extractor.url_safety import PublicFetch, UrlSafetyError


@dataclass(frozen=True, slots=True)
class PdfText:
    title: str
    author: str | None
    markdown: str
    page_count: int


class PdfFallbackRequired(RuntimeError):
    """The complete PDF needs the existing provider parser/OCR fallback."""


def is_pdf(document: PublicFetch) -> bool:
    media_type = (document.content_type or "").split(";", 1)[0].strip().lower()
    return media_type == "application/pdf" or document.body.lstrip().startswith(b"%PDF-")


async def _run_tool(*arguments: str, max_bytes: int, timeout_seconds: float) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
    except OSError as exc:
        raise PdfFallbackRequired("Local PDF tools are unavailable") from exc
    try:
        async with asyncio.timeout(timeout_seconds):
            assert process.stdout is not None
            output = bytearray()
            while chunk := await process.stdout.read(65_536):
                if len(output) + len(chunk) > max_bytes:
                    raise UrlSafetyError(
                        "Complete PDF text exceeds the byte limit; no pages were omitted",
                        code="response_too_large",
                    )
                output.extend(chunk)
            if await process.wait() != 0:
                raise PdfFallbackRequired("Local PDF parser could not read the complete document")
            return bytes(output)
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


async def extract_pdf_text(
    document: PublicFetch, *, max_markdown_bytes: int, timeout_seconds: float
) -> PdfText:
    with TemporaryDirectory(prefix="newsly-pdf-") as directory:
        pdf_path = Path(directory) / "document.pdf"
        pdf_path.write_bytes(document.body)
        info = (
            await _run_tool(
                "pdfinfo", str(pdf_path), max_bytes=65_536, timeout_seconds=timeout_seconds
            )
        ).decode("utf-8", errors="replace")
        fields = dict(re.findall(r"^([A-Za-z ]+):\s*(.*?)$", info, re.MULTILINE))
        try:
            page_count = int(fields["Pages"])
        except (KeyError, ValueError) as exc:
            raise PdfFallbackRequired("Local PDF parser did not report a page count") from exc
        if page_count < 1:
            raise PdfFallbackRequired("PDF has no readable pages")
        raw_text = await _run_tool(
            "pdftotext",
            "-layout",
            "-enc",
            "UTF-8",
            str(pdf_path),
            "-",
            max_bytes=max_markdown_bytes,
            timeout_seconds=timeout_seconds,
        )
        pages = raw_text.decode("utf-8", errors="replace").split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        if len(pages) != page_count:
            raise PdfFallbackRequired("Local PDF text does not cover every reported page")
        images = (
            await _run_tool(
                "pdfimages",
                "-list",
                str(pdf_path),
                max_bytes=1_000_000,
                timeout_seconds=timeout_seconds,
            )
        ).decode("utf-8", errors="replace")
        image_pages = {
            int(match) for match in re.findall(r"^\s*(\d+)\s+\d+\s", images, re.MULTILINE)
        }
        if any(
            number in image_pages and sum(character.isalpha() for character in text) < 50
            for number, text in enumerate(pages, start=1)
        ):
            raise PdfFallbackRequired("PDF contains scanned/image pages that require OCR")
        if sum(character.isalpha() for character in "".join(pages)) < 100:
            raise PdfFallbackRequired(
                "PDF has insufficient readable text; provider parsing required"
            )
        markdown = "\n\n".join(
            f"## Page {number}\n\n{text.strip()}" for number, text in enumerate(pages, start=1)
        )
        if len(markdown.encode("utf-8")) > max_markdown_bytes:
            raise UrlSafetyError(
                "Complete PDF text exceeds the byte limit; no pages were omitted",
                code="response_too_large",
            )
        filename = unquote(urlsplit(document.final_url).path.rsplit("/", 1)[-1])
        return PdfText(
            title=(fields.get("Title", "").strip() or filename or "PDF document")[:1_000],
            author=(fields.get("Author", "").strip()[:500] or None),
            markdown=markdown,
            page_count=page_count,
        )
