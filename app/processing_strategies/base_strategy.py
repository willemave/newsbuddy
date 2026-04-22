"""
This module defines the abstract base class for URL processing strategies.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable
from typing import Any

import httpx  # For type hinting httpx.Headers

from app.http_client.robust_http_client import RobustHttpClient


class UrlProcessorStrategy(ABC):
    """
    Abstract base class for URL processing strategies.
    Each concrete strategy will handle a specific type of URL or content.
    """

    def __init__(self, http_client: RobustHttpClient):
        """
        Initializes the strategy with a RobustHttpClient instance.

        Args:
            http_client: An instance of RobustHttpClient for making network requests.
        """
        self.http_client = http_client

    def preprocess_url(self, url: str) -> str:
        """
        Optional method to normalize, clean, or otherwise transform a URL
        before attempting to download.
        Default implementation returns the URL as is.

        Args:
            url: The original URL.

        Returns:
            The processed URL.
        """
        return url

    @abstractmethod
    def can_handle_url(self, url: str, response_headers: httpx.Headers | None = None) -> bool:
        """
        Determines if this strategy can handle the given URL or content type.
        This method might be called by a factory after making a HEAD request.

        Args:
            url: The URL to check.
            response_headers: Optional headers from a HEAD request to the URL.

        Returns:
            True if the strategy can handle this URL/content, False otherwise.
        """
        pass

    @abstractmethod
    def download_content(self, url: str) -> Any:
        """
        Downloads the content from the given URL.
        The return type depends on the content (e.g., bytes for PDF, str for HTML).

        Args:
            url: The URL to download content from.

        Returns:
            The downloaded content (e.g., str for HTML, bytes for PDF).
        """
        pass

    @abstractmethod
    def extract_data(
        self,
        content: Any,
        url: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | Awaitable[dict[str, Any]]:
        """
        Extracts relevant data from the downloaded content.
        Should return a standardized dictionary.

        Args:
            content: The downloaded content (e.g., HTML string, PDF bytes).
            url: The URL from which the content was downloaded (final URL after redirects).
            context: Optional caller-provided context for the extraction attempt.

        Returns:
            A dictionary containing extracted data like title, author, text_content, etc.
            Example structure:
            {
                "title": "Article Title",
                "author": "Author Name",
                "publication_date": "YYYY-MM-DD", # or datetime object
                "text_content": "Main text content...", # For HTML/text types
                "binary_content_b64": "base64_encoded_pdf_if_applicable", # For PDF types
                "content_type": "html" # or "pdf", "pubmed_delegation"
                "final_url_after_redirects": "...",
                "original_url_from_db": "..." # Passed through or added by main processor
            }
            For delegation (e.g. PubMedStrategy):
            {
                "next_url_to_process": "https://actual_content_url.com/article.pdf",
                "original_pubmed_url": "original_url_from_db",
                "content_type": "pubmed_delegation"
            }
        """
        pass

    @abstractmethod
    def prepare_for_llm(
        self,
        extracted_data: dict[str, Any],
    ) -> dict[str, Any] | Awaitable[dict[str, Any]]:
        """
        Prepares the extracted data into a format suitable for LLM processing
        (filtering and summarization) based on app.llm functions.

        Args:
            extracted_data: The dictionary returned by extract_data.

        Returns:
            A dictionary structured for input to app.llm functions.
            Example structure:
            {
                "content_to_filter": "text_for_filtering", # if applicable
                "content_to_summarize": "text_or_bytes_for_summarization",
                "is_pdf": False # or True
            }
        """
        pass

    def extract_internal_urls(self, content: Any, original_url: str) -> list[str]:
        """
        Optional method to identify and extract relevant URLs found within the
        processed content. For logging related links.
        Default implementation returns an empty list.

        Args:
            content: The downloaded content.
            original_url: The original URL that was processed.

        Returns:
            A list of extracted internal URLs.
        """
        return []
