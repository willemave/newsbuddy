from typing import Any, cast

import httpx
from google import genai
from google.genai.types import Part

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.base_strategy import UrlProcessorStrategy
from app.services.http import NonRetryableError
from app.services.langfuse_tracing import (
    extract_google_usage_details,
    langfuse_generation_context,
)
from app.services.pdf_text_extraction import extract_pdf_text

logger = get_logger(__name__)
settings = get_settings()


class PdfProcessorStrategy(UrlProcessorStrategy):
    """Strategy for processing PDF documents."""

    def __init__(self, http_client: RobustHttpClient):
        super().__init__(http_client)
        google_api_key = getattr(settings, "google_api_key", None)
        if not google_api_key:
            raise ValueError("Google API key is required for PDF processing")
        self.client = genai.Client(api_key=google_api_key)
        self.model_name = getattr(settings, "pdf_gemini_model", "gemini-3.1-flash-lite-preview")

    def can_handle_url(self, url: str, response_headers: httpx.Headers | None = None) -> bool:
        """Check if this strategy can handle the given URL."""
        # Exclude arxiv URLs - they should be handled by ArxivProcessorStrategy
        if "arxiv.org" in url.lower():
            return False

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
        # No special preprocessing needed for general PDFs
        # Arxiv URLs are handled by ArxivProcessorStrategy
        return url

    def download_content(self, url: str) -> bytes:
        """Download PDF content from the given URL."""
        logger.info(f"PdfStrategy: Downloading PDF content from {url}")
        try:
            response = self.http_client.get(url)
            logger.info(
                f"PdfStrategy: Successfully downloaded PDF from {url}. Final URL: {response.url}"
            )
            return response.content  # Returns PDF as bytes
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            # 4xx client errors are non-retryable (403 Forbidden, 404 Not Found, etc.)
            if 400 <= status_code < 500:
                logger.warning(f"PdfStrategy: HTTP {status_code} for {url} - marking as failed")
                raise NonRetryableError(f"HTTP {status_code}: {e}") from e
            raise

    def extract_data(
        self,
        content: bytes,
        url: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract text from PDF content using Google Gemini API."""
        del context
        logger.info(f"PdfStrategy: Extracting text from PDF content for URL: {url}")

        try:
            if not self.model_name:
                raise NonRetryableError("PDF_GEMINI_MODEL is not configured")
            # Create a Part object from PDF bytes
            pdf_part = Part.from_bytes(data=content, mime_type="application/pdf")

            # Simple extraction prompt - just get the text
            extraction_prompt = """
            Extract all text content from this PDF document.
            Return the full text in a clean, readable format.
            Preserve the document structure (headings, paragraphs, lists).
            If you can identify the title, include it at the beginning.
            """

            with langfuse_generation_context(
                name="queue.pdf.extract_text",
                model=self.model_name,
                input_data=extraction_prompt,
                metadata={"source": "queue", "url": url},
            ) as generation:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=cast(Any, [pdf_part, extraction_prompt]),
                    config={"temperature": 0.3, "max_output_tokens": 50000},
                )
                usage_details = extract_google_usage_details(response)
                response_text = getattr(response, "text", None)
                if generation is not None:
                    generation.update(
                        output=response_text[:400] if isinstance(response_text, str) else None,
                        usage_details=usage_details,
                    )

            # Get the extracted text
            text_content = response.text if hasattr(response, "text") else ""
            if not text_content:
                raise ValueError("No text extracted from PDF")
            return self._build_extracted_data(text_content, url=url, default_title="PDF Document")
        except Exception as e:
            logger.error(f"PdfStrategy: Failed to extract text from PDF {url}: {e}")
            fallback_text = extract_pdf_text(content)
            if fallback_text:
                logger.info(
                    "PdfStrategy: Local PDF extraction succeeded for %s after Gemini failure",
                    url,
                )
                return self._build_extracted_data(
                    fallback_text,
                    url=url,
                    default_title="PDF Document",
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
