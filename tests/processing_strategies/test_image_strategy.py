"""
Tests for the ImageProcessorStrategy.
"""

from unittest.mock import Mock

import httpx
import pytest

from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.image_strategy import ImageProcessorStrategy


class TestImageProcessorStrategy:
    """Test cases for ImageProcessorStrategy."""

    @pytest.fixture
    def mock_http_client(self):
        """Create a mock HTTP client."""
        return Mock(spec=RobustHttpClient)

    @pytest.fixture
    def image_strategy(self, mock_http_client):
        """Create an ImageProcessorStrategy instance."""
        return ImageProcessorStrategy(mock_http_client)

    def test_can_handle_url_by_extension(self, image_strategy):
        """Test that strategy can identify image URLs by file extension."""
        image_urls = [
            "https://example.com/image.jpg",
            "https://example.com/photo.jpeg",
            "https://example.com/picture.png",
            "https://example.com/animation.gif",
            "https://example.com/modern.webp",
            "https://example.com/vector.svg",
            "https://example.com/bitmap.bmp",
            "https://example.com/icon.ico",
            "https://example.com/high-res.tiff",
        ]

        for url in image_urls:
            assert image_strategy.can_handle_url(url), f"Should handle {url}"

    def test_can_handle_url_by_content_type(self, image_strategy):
        """Test that strategy can identify image URLs by Content-Type header."""
        headers = httpx.Headers({"content-type": "image/jpeg"})
        assert image_strategy.can_handle_url("https://example.com/photo", headers)

        headers = httpx.Headers({"content-type": "image/png; charset=utf-8"})
        assert image_strategy.can_handle_url("https://example.com/image", headers)

        headers = httpx.Headers({"content-type": "image/svg+xml"})
        assert image_strategy.can_handle_url("https://example.com/vector", headers)

    def test_can_handle_url_by_format_parameter(self, image_strategy):
        """Test that strategy can identify image URLs by format parameter."""
        urls_with_format = [
            "https://example.com/api/image?format=jpg",
            "https://example.com/resize?format=png&width=100",
            "https://example.com/convert?format=webp",
        ]

        for url in urls_with_format:
            assert image_strategy.can_handle_url(url), f"Should handle {url}"

    def test_cannot_handle_non_image_urls(self, image_strategy):
        """Test that strategy rejects non-image URLs."""
        non_image_urls = [
            "https://example.com/article.html",
            "https://example.com/document.pdf",
            "https://example.com/data.json",
            "https://example.com/script.js",
            "https://example.com/style.css",
            "https://example.com/page",
        ]

        for url in non_image_urls:
            assert not image_strategy.can_handle_url(url), f"Should not handle {url}"

    def test_cannot_handle_non_image_content_type(self, image_strategy):
        """Test that strategy rejects non-image Content-Type headers."""
        headers = httpx.Headers({"content-type": "text/html"})
        assert not image_strategy.can_handle_url("https://example.com/page", headers)

        headers = httpx.Headers({"content-type": "application/pdf"})
        assert not image_strategy.can_handle_url("https://example.com/doc", headers)

    def test_download_content_returns_url(self, image_strategy):
        """Test that download_content returns the URL without actually downloading."""
        url = "https://example.com/image.jpg"
        result = image_strategy.download_content(url)
        assert result == url

    def test_extract_data_marks_for_skipping(self, image_strategy):
        """Test that extract_data returns proper structure for skipping."""
        url = "https://example.com/photo.jpg"
        content = url  # From download_content

        result = image_strategy.extract_data(content, url)

        assert result["content_type"] == "image"
        assert result["skip_processing"] is True
        assert result["image_url"] == url
        assert result["final_url_after_redirects"] == url
        assert result["text_content"] == ""
        assert result["title"] == "photo.jpg"

    def test_extract_data_handles_complex_urls(self, image_strategy):
        """Test that extract_data handles URLs with query parameters."""
        url = "https://example.com/images/photo.jpg?size=large&quality=high"
        content = url

        result = image_strategy.extract_data(content, url)

        assert result["title"] == "photo.jpg"
        assert result["image_url"] == url

    def test_prepare_for_llm_returns_empty_content(self, image_strategy):
        """Test that prepare_for_llm returns empty content for skipping."""
        extracted_data = {"title": "image.jpg", "content_type": "image", "skip_processing": True}

        result = image_strategy.prepare_for_llm(extracted_data)

        assert result["content_to_filter"] == ""
        assert result["content_to_summarize"] == ""
        assert result["is_pdf"] is False
        assert result["skip_processing"] is True

    def test_extract_internal_urls_returns_empty(self, image_strategy):
        """Test that extract_internal_urls returns empty list."""
        result = image_strategy.extract_internal_urls("content", "https://example.com/image.jpg")
        assert result == []
