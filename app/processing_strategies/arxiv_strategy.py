"""Strategy for processing arXiv content URLs."""

from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx  # For type hinting httpx.Headers

from app.core.logging import get_logger
from app.core.model_defaults import PDF_EXTRACTION_MODEL_NAME
from app.core.settings import get_settings
from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.base_strategy import UrlProcessorStrategy
from app.services.arxiv_metadata import fetch_arxiv_source_metadata
from app.services.openai_pdf_extraction import (
    classify_openai_pdf_error,
    create_openai_pdf_client,
    extract_pdf_with_openai,
)
from app.services.prompt_library import load_prompt
from app.services.source_metadata import attach_source_metadata, dump_source_metadata

logger = get_logger(__name__)
settings = get_settings()


class ArxivProcessorStrategy(UrlProcessorStrategy):
    """
    Strategy for processing arXiv URLs, whether they are abstract pages or direct PDFs.
    Abstract links are normalized to their PDF counterparts before download.
    """

    def __init__(self, http_client: RobustHttpClient):
        super().__init__(http_client)
        self._logger_prefix = "ArxivStrategy"

    def can_handle_url(self, url: str, response_headers: httpx.Headers | None = None) -> bool:
        """
        Determines if this strategy can handle the given URL.
        """
        parsed = urlparse(url)
        if not self._is_arxiv_host(parsed.netloc):
            logger.debug("%s cannot handle URL (not arXiv host): %s", self._logger_prefix, url)
            return False

        path = parsed.path.lower()
        if path.startswith("/abs/") or path.startswith("/pdf/"):
            logger.debug("%s can handle URL: %s", self._logger_prefix, url)
            return True

        logger.debug("%s cannot handle URL (unsupported path): %s", self._logger_prefix, url)
        return False

    def preprocess_url(self, url: str) -> str:
        """
        Normalize arXiv URLs so downstream processing always receives a direct PDF URL.
        """
        parsed = urlparse(url)
        if not self._is_arxiv_host(parsed.netloc):
            logger.warning(
                "%s: preprocess_url called with non-arXiv host %s; returning unchanged.",
                self._logger_prefix,
                url,
            )
            return url

        path = parsed.path
        lower_path = path.lower()
        target_path = path
        target_query = parsed.query

        if lower_path.startswith("/abs/"):
            target_path = f"/pdf/{path[5:]}"
            target_query = ""
            logger.info(
                "%s: Converted arXiv abstract URL %s to PDF path %s",
                self._logger_prefix,
                url,
                target_path,
            )
        elif lower_path.startswith("/pdf/"):
            target_path = f"/pdf/{path[5:]}"
        else:
            logger.warning(
                "%s: preprocess_url received unsupported arXiv path %s; returning unchanged.",
                self._logger_prefix,
                url,
            )
            return url

        normalized = parsed._replace(
            scheme="https" if parsed.scheme in ("", "http") else parsed.scheme,
            netloc="arxiv.org",
            path=target_path,
            params="",
            query=target_query,
            fragment="",
        )
        return urlunparse(normalized)

    def download_content(self, url: str) -> bytes:  # PDF content is bytes
        """
        Downloads the PDF content from the given URL.
        This method expects 'url' to be a direct link to a PDF file
        (transformed by preprocess_url if it was an abstract page).
        """
        logger.info(f"ArxivStrategy: Downloading PDF content from {url}")
        response = self.http_client.get(url)
        # RobustHttpClient handles raise_for_status
        logger.info(
            f"ArxivStrategy: Successfully downloaded PDF from {url}. Final URL: {response.url}"
        )
        return response.content

    def extract_data(
        self,
        content: bytes,
        url: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Prepares PDF data for LLM processing.
        Extract text and visual content through OpenAI's native PDF input.
        """
        del context
        logger.info("ArxivStrategy: Preparing PDF data for LLM processing for URL: %s", url)
        source_metadata = self._source_metadata_payload(url)

        if not content:
            logger.warning(f"ArxivStrategy: No PDF content provided for {url}")
            extracted_data = {
                "title": "Extraction Failed (No PDF Content)",
                "text_content": None,
                "content_type": "pdf",
                "final_url_after_redirects": url,
            }
            return attach_source_metadata(extracted_data, source_metadata)

        openai_api_key = getattr(settings, "openai_api_key", None)
        model_name = getattr(
            settings,
            "pdf_extraction_model",
            PDF_EXTRACTION_MODEL_NAME,
        )
        if openai_api_key:
            try:
                client = create_openai_pdf_client(openai_api_key)
                text_content = extract_pdf_with_openai(
                    client,
                    content=content,
                    model=model_name,
                    prompt=load_prompt("processing/pdf_extract_text"),
                )
                if text_content:
                    return self._build_extracted_data(
                        text_content,
                        url=url,
                        default_title="ArXiv PDF Document",
                        source_metadata=source_metadata,
                    )
            except Exception as exc:  # noqa: BLE001
                error_classification = classify_openai_pdf_error(exc)
                logger.error(
                    "ArxivStrategy: OpenAI PDF extraction failed for %s (%s): %s",
                    url,
                    error_classification,
                    exc,
                    extra={
                        "component": "arxiv_strategy",
                        "operation": "openai_pdf_extract",
                        "context_data": {
                            "url": url,
                            "model": model_name,
                            "error_classification": error_classification,
                        },
                    },
                )
        else:
            logger.warning(
                "ArxivStrategy: OpenAI API key missing; cannot extract PDF text for %s",
                url,
            )

        parsed_url = urlparse(url)
        filename = parsed_url.path.split("/")[-1] or "ArXiv PDF Document"
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"

        logger.info(
            "ArxivStrategy: Successfully prepared PDF data for %s. Fallback title: %s",
            url,
            filename,
        )
        extracted_data = {
            "title": filename,  # Fallback title - LLM will extract the real title
            "author": None,
            "publication_date": None,
            "text_content": "",
            "content_type": "pdf",
            "final_url_after_redirects": url,
        }
        return attach_source_metadata(extracted_data, source_metadata)

    def prepare_for_llm(self, extracted_data: dict[str, Any]) -> dict[str, Any]:
        """
        Prepare extracted PDF text for summarization.
        """
        final_url = extracted_data.get("final_url_after_redirects", "Unknown URL")
        logger.info("ArxivStrategy: Preparing PDF data for LLM for URL: %s", final_url)
        text_content = extracted_data.get("text_content") or ""
        return {
            "content_to_filter": text_content,
            "content_to_summarize": text_content,
            "is_pdf": True,
        }

    def extract_internal_urls(self, content: bytes, original_url: str) -> list[str]:
        """
        Extracts internal URLs. For PDFs, this is typically not applicable in the same
        way as HTML, so an empty list is returned.
        """
        logger.info(
            "ArxivStrategy: extract_internal_urls called for %s (PDF). Returning empty list.",
            original_url,
        )
        return []

    def _is_arxiv_host(self, netloc: str) -> bool:
        """Return True if the provided netloc belongs to arxiv.org."""
        normalized = netloc.lower()
        return normalized == "arxiv.org" or normalized.endswith(".arxiv.org")

    @staticmethod
    def _build_extracted_data(
        text_content: str,
        *,
        url: str,
        default_title: str,
        source_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        lines = text_content.strip().split("\n")
        title = lines[0][:200] if lines else default_title
        logger.info(
            "ArxivStrategy: Extracted text for %s. Title: %s...",
            url,
            title[:50],
        )
        extracted_data = {
            "title": title,
            "author": None,
            "publication_date": None,
            "text_content": text_content,
            "content_type": "pdf",
            "final_url_after_redirects": url,
        }
        return attach_source_metadata(extracted_data, source_metadata)

    def _source_metadata_payload(self, url: str) -> dict[str, Any] | None:
        """Return optional arXiv source metadata for display-only API surfaces."""
        source_metadata = fetch_arxiv_source_metadata(url, http_client=self.http_client)
        if source_metadata is None:
            return None
        return dump_source_metadata(source_metadata)
