"""Plain-text URL processing strategy."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from app.core.logging import get_logger
from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.base_strategy import UrlProcessorStrategy
from app.services.http import NonRetryableError

logger = get_logger(__name__)


class PlainTextProcessorStrategy(UrlProcessorStrategy):
    """Process directly linked plain-text documents."""

    TEXT_EXTENSIONS = {".txt", ".text"}
    TEXT_MIME_TYPES = {
        "text/plain",
        "text/markdown",
        "text/x-markdown",
    }

    def __init__(self, http_client: RobustHttpClient):
        super().__init__(http_client)

    def can_handle_url(self, url: str, response_headers: httpx.Headers | None = None) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False

        if response_headers:
            content_type = response_headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type in self.TEXT_MIME_TYPES:
                return True

        path = parsed.path.lower()
        return any(path.endswith(extension) for extension in self.TEXT_EXTENSIONS)

    def download_content(self, url: str) -> str:
        logger.info("PlainTextStrategy: Downloading text content from %s", url)
        try:
            response = self.http_client.get(
                url,
                headers={"Accept": "text/plain,text/markdown;q=0.9,*/*;q=0.5"},
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if 400 <= status_code < 500:
                raise NonRetryableError(f"HTTP {status_code}: {exc}") from exc
            raise
        return response.text

    def extract_data(
        self,
        content: str,
        url: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del context
        text_content = content.strip()
        return {
            "title": self._title_from_text(text_content, url=url),
            "author": None,
            "publication_date": None,
            "text_content": text_content,
            "content_type": "text",
            "final_url_after_redirects": url,
            "extraction_error": None if text_content else "Plain text response was empty",
        }

    def prepare_for_llm(self, extracted_data: dict[str, Any]) -> dict[str, Any]:
        text_content = (extracted_data.get("text_content") or "").strip()
        return {
            "content_to_filter": text_content,
            "content_to_summarize": text_content,
            "is_pdf": False,
        }

    @classmethod
    def _title_from_text(cls, text_content: str, *, url: str) -> str:
        for line in text_content.splitlines():
            title = " ".join(line.split()).strip()
            if title:
                return title[:200]

        filename = unquote(urlparse(url).path.rsplit("/", 1)[-1]).strip()
        return filename or "Plain Text Document"
