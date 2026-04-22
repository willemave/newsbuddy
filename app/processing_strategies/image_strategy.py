"""
This module defines the strategy for handling image URLs.
It identifies image URLs and skips processing them.
"""

from typing import Any

import httpx

from app.core.logging import get_logger
from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.base_strategy import UrlProcessorStrategy

logger = get_logger(__name__)


class ImageProcessorStrategy(UrlProcessorStrategy):
    """
    Strategy for handling image URLs.
    This strategy identifies image URLs and skips processing them,
    allowing the article detail view to render the image directly.
    """

    # Common image file extensions
    IMAGE_EXTENSIONS = {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".bmp",
        ".ico",
        ".tiff",
        ".tif",
    }

    # Common image MIME types
    IMAGE_MIME_TYPES = {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/svg+xml",
        "image/bmp",
        "image/x-icon",
        "image/vnd.microsoft.icon",
        "image/tiff",
    }

    def __init__(self, http_client: RobustHttpClient):
        super().__init__(http_client)

    def can_handle_url(self, url: str, response_headers: httpx.Headers | None = None) -> bool:
        """
        Determines if this strategy can handle the given URL.
        Checks for image file extensions or image MIME types.
        """
        # Check Content-Type header first if available
        if response_headers:
            content_type = response_headers.get("content-type", "").lower()
            for mime_type in self.IMAGE_MIME_TYPES:
                if mime_type in content_type:
                    logger.debug(
                        f"ImageStrategy can handle {url} based on Content-Type: {content_type}"
                    )
                    return True

        # Check URL extension as fallback
        url_lower = url.lower()
        for ext in self.IMAGE_EXTENSIONS:
            if url_lower.endswith(ext):
                logger.debug(f"ImageStrategy can handle {url} based on file extension: {ext}")
                return True

        # Check for common image URL patterns (e.g., query parameters)
        if any(f"format={fmt}" in url_lower for fmt in ["jpg", "jpeg", "png", "gif", "webp"]):
            logger.debug(f"ImageStrategy can handle {url} based on format parameter")
            return True

        logger.debug(f"ImageStrategy cannot handle {url}")
        return False

    def download_content(self, url: str) -> str:
        """
        For image strategy, we don't actually download the content.
        We just return the URL as the "content" since we're skipping processing.
        """
        logger.info(f"ImageStrategy: Skipping download for image URL: {url}")
        return url

    def extract_data(
        self,
        content: str,
        url: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        For image URLs, we return a special structure indicating this should be skipped.
        The content parameter here is actually the URL from download_content.
        """
        del context
        logger.info(f"ImageStrategy: Marking image URL for skipping: {url}")

        # Extract a basic title from the URL
        title = url.split("/")[-1] if "/" in url else url
        if "?" in title:
            title = title.split("?")[0]
        if not title or title == url:
            title = "Image"

        return {
            "title": title,
            "author": None,
            "publication_date": None,
            "text_content": "",
            "content_type": "image",
            "final_url_after_redirects": url,
            "skip_processing": True,  # Special flag to indicate this should be skipped
            "image_url": url,  # Store the image URL for potential use in templates
        }

    def prepare_for_llm(self, extracted_data: dict[str, Any]) -> dict[str, Any]:
        """
        For image content, we return empty content since there's nothing to process.
        """
        logger.info("ImageStrategy: Preparing empty data for LLM (image will be skipped)")
        return {
            "content_to_filter": "",
            "content_to_summarize": "",
            "is_pdf": False,
            "skip_processing": True,
        }

    def extract_internal_urls(self, content: str, original_url: str) -> list[str]:
        """
        Images don't contain internal URLs to extract.
        """
        return []
