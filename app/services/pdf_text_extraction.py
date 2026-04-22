"""Shared local PDF text extraction helpers."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from app.core.logging import get_logger

logger = get_logger(__name__)


def extract_pdf_text(content: bytes) -> str:
    """Extract text from PDF bytes with a local parser fallback."""
    if not content:
        return ""

    try:
        reader = PdfReader(BytesIO(content))
    except Exception:  # noqa: BLE001
        logger.exception("Failed to initialize PDF reader")
        return ""

    extracted_pages: list[str] = []
    for index, page in enumerate(reader.pages):
        try:
            page_text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to extract text from PDF page %s",
                index,
                extra={
                    "component": "pdf_text_extraction",
                    "operation": "extract_page",
                    "context_data": {"page_index": index},
                },
            )
            continue
        normalized = page_text.strip()
        if normalized:
            extracted_pages.append(normalized)

    return "\n\n".join(extracted_pages).strip()
