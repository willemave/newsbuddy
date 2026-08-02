from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.model_defaults import PDF_EXTRACTION_MODEL_NAME
from app.core.settings import get_settings
from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.base_strategy import UrlProcessorStrategy
from app.services.github_urls import normalize_github_file_url_to_raw, parse_github_file_url
from app.services.http import NonRetryableError
from app.services.openai_pdf_extraction import (
    classify_openai_pdf_error,
    create_openai_pdf_client,
    extract_pdf_with_openai,
)
from app.services.prompt_library import load_prompt

logger = get_logger(__name__)
settings = get_settings()

PDF_MAGIC = b"%PDF-"


class PdfProcessorStrategy(UrlProcessorStrategy):
    """Strategy for processing PDF documents."""

    def __init__(self, http_client: RobustHttpClient):
        super().__init__(http_client)
        openai_api_key = getattr(settings, "openai_api_key", None)
        if not openai_api_key:
            raise ValueError("OpenAI API key is required for PDF processing")
        self.client = create_openai_pdf_client(openai_api_key)
        self.model_name = getattr(
            settings,
            "pdf_extraction_model",
            PDF_EXTRACTION_MODEL_NAME,
        )

    def can_handle_url(self, url: str, response_headers: httpx.Headers | None = None) -> bool:
        """Check if this strategy can handle the given URL."""
        # Exclude arxiv URLs - they should be handled by ArxivProcessorStrategy
        if "arxiv.org" in url.lower():
            return False

        github_file = parse_github_file_url(url)
        if github_file is not None:
            return github_file.is_pdf

        # Check URL extension
        if url.lower().endswith(".pdf"):
            return True

        # Check content type
        if response_headers:
            content_type = response_headers.get("content-type", "").lower()
            return "application/pdf" in content_type

        return False

    def preprocess_url(self, url: str) -> str:
        """Preprocess PDF URLs."""
        return normalize_github_file_url_to_raw(url) or url

    def download_content(self, url: str) -> bytes:
        """Download PDF content from the given URL."""
        download_url = self.preprocess_url(url)
        logger.info(f"PdfStrategy: Downloading PDF content from {download_url}")
        try:
            response = self.http_client.get(download_url)
            logger.info(
                "PdfStrategy: Successfully downloaded PDF from %s. Final URL: %s",
                download_url,
                response.url,
            )
            content = response.content
            if not _looks_like_pdf(content):
                raise NonRetryableError(
                    f"Downloaded content is not a PDF: {response.url or download_url}"
                )
            return content  # Returns PDF as bytes
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            # 4xx client errors are non-retryable (403 Forbidden, 404 Not Found, etc.)
            if 400 <= status_code < 500:
                logger.warning(
                    "PdfStrategy: HTTP %s for %s - marking as failed",
                    status_code,
                    download_url,
                )
                raise NonRetryableError(f"HTTP {status_code}: {e}") from e
            raise

    def extract_data(
        self,
        content: bytes,
        url: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract PDF text and visual content using OpenAI's native PDF input."""
        del context
        logger.info(f"PdfStrategy: Extracting text from PDF content for URL: {url}")
        if not _looks_like_pdf(content):
            raise NonRetryableError(f"Downloaded content is not a PDF: {url}")

        try:
            if not self.model_name:
                raise NonRetryableError("PDF_EXTRACTION_MODEL is not configured")
            text_content = extract_pdf_with_openai(
                self.client,
                content=content,
                model=self.model_name,
                prompt=load_prompt("processing/pdf_extract_text"),
            )
            if not text_content:
                raise ValueError("No text extracted from PDF")
            return self._build_extracted_data(text_content, url=url, default_title="PDF Document")
        except Exception as e:
            error_classification = classify_openai_pdf_error(e)
            logger.error(
                "PdfStrategy: OpenAI PDF extraction failed for %s (%s): %s",
                url,
                error_classification,
                e,
                extra={
                    "component": "pdf_strategy",
                    "operation": "openai_pdf_extract",
                    "context_data": {
                        "url": url,
                        "model": self.model_name,
                        "error_classification": error_classification,
                    },
                },
            )
            return {
                "title": "PDF Extraction Failed",
                "text_content": "",
                "content_type": "pdf",
                "final_url_after_redirects": url,
            }

    def prepare_for_llm(self, extracted_data: dict[str, Any]) -> dict[str, Any]:
        """Prepare extracted PDF data for LLM processing."""
        final_url = extracted_data.get("final_url_after_redirects", "Unknown URL")
        logger.info(f"PdfStrategy: Preparing data for LLM for URL: {final_url}")
        text_content = extracted_data.get("text_content") or ""

        return {
            "content_to_filter": text_content,
            "content_to_summarize": text_content,
            "is_pdf": True,
        }

    @staticmethod
    def _build_extracted_data(
        text_content: str,
        *,
        url: str,
        default_title: str,
    ) -> dict[str, Any]:
        lines = text_content.strip().split("\n")
        title = lines[0][:200] if lines else default_title

        logger.info(
            "PdfStrategy: Successfully extracted text for %s. Title: %s...",
            url,
            title[:50],
        )
        return {
            "title": title,
            "author": None,
            "publication_date": None,
            "text_content": text_content,
            "content_type": "pdf",
            "final_url_after_redirects": url,
        }


def _looks_like_pdf(content: bytes) -> bool:
    """Return true when downloaded bytes have a PDF header near the start."""
    return content[:1024].lstrip().startswith(PDF_MAGIC)
