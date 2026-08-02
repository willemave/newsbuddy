"""Test HTML strategy event loop handling."""

from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

from app.processing_strategies.html_strategy import HtmlProcessorStrategy


def _build_crawler(mock_result: MagicMock) -> AsyncMock:
    crawler = AsyncMock()
    crawler.arun = AsyncMock(return_value=mock_result)
    crawler.start = AsyncMock(return_value=crawler)
    crawler.close = AsyncMock(return_value=None)
    return crawler


class TestHtmlStrategyEventLoop:
    """Test HTML strategy handling of event loop conflicts."""

    def test_html_strategy_async_extract_data(self):
        """Test that HTML strategy correctly uses async extract_data."""
        mock_http_client = MagicMock()
        strategy = HtmlProcessorStrategy(mock_http_client)

        with patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler"
        ) as mock_crawler_class:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.markdown = MagicMock()
            mock_result.markdown.raw_markdown = "Test content extracted from page"
            mock_result.metadata = {"title": "Test Article"}
            mock_result.url = "https://example.com/article"
            mock_result.cleaned_html = "<html><body>Test content</body></html>"

            crawler = _build_crawler(mock_result)
            mock_crawler_class.return_value = crawler

            result = strategy.extract_data("dummy_content", "https://example.com/article")

            assert result["title"] == "Test Article"
            assert result["text_content"] == "Test content extracted from page"
            assert result["final_url_after_redirects"] == "https://example.com/article"
            assert result["content_type"] == "html"

            crawler.arun.assert_called_once()

    def test_html_strategy_error_handling(self):
        """Test that HTML strategy handles extraction errors gracefully."""
        mock_http_client = MagicMock()
        strategy = HtmlProcessorStrategy(mock_http_client)
        url = "https://example.com/article"

        with (
            patch(
                "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler"
            ) as mock_crawler_class,
            patch.object(strategy, "_firecrawl_fallback_fetch", return_value=None),
            patch.object(strategy, "_should_use_extraction_fallback", return_value=False),
        ):
            crawler = AsyncMock()
            crawler.arun = AsyncMock(side_effect=Exception("Crawl4ai extraction failed"))
            crawler.start = AsyncMock(return_value=crawler)
            crawler.close = AsyncMock(return_value=None)
            mock_crawler_class.return_value = crawler

            result = strategy.extract_data("dummy_content", url)

            assert result["title"] == f"Content from {url}"
            assert "Failed to extract content" in result["text_content"]
            assert result["content_type"] == "html"
            assert result["extraction_error"] == "Crawl4ai extraction failed"

    def test_html_strategy_concurrent_extractions(self):
        """Test that HTML strategy can handle sequential extractions."""
        mock_http_client = MagicMock()
        strategy = HtmlProcessorStrategy(mock_http_client)

        with patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler"
        ) as mock_crawler_class:

            def create_mock_result(url):
                mock_result = MagicMock()
                mock_result.success = True
                mock_result.markdown = MagicMock()
                mock_result.markdown.raw_markdown = f"Content from {url}"
                mock_result.metadata = {"title": f"Title for {url}"}
                mock_result.url = url
                mock_result.cleaned_html = f"<html><body>Content from {url}</body></html>"
                return mock_result

            crawler = AsyncMock()
            crawler.arun = AsyncMock(side_effect=lambda url, config: create_mock_result(url))
            crawler.start = AsyncMock(return_value=crawler)
            crawler.close = AsyncMock(return_value=None)
            mock_crawler_class.return_value = crawler

            urls = [
                "https://example.com/article1",
                "https://example.com/article2",
                "https://example.com/article3",
            ]

            results = [strategy.extract_data("dummy", url) for url in urls]

            assert len(results) == 3
            for i, result in enumerate(results):
                expected_url = urls[i]
                assert result["title"] == f"Title for {expected_url}"
                assert result["text_content"] == f"Content from {expected_url}"
                assert result["final_url_after_redirects"] == expected_url

    def test_html_strategy_with_different_sources(self):
        """Test that HTML strategy sets source based on the final URL domain."""
        mock_http_client = MagicMock()
        strategy = HtmlProcessorStrategy(mock_http_client)

        test_cases = [
            "https://example.substack.com/p/article",
            "https://medium.com/@user/article",
            "https://pubmed.ncbi.nlm.nih.gov/12345",
            "https://arxiv.org/abs/2301.00001",
            "https://chinatalk.media/p/article",
            "https://example.com/article",
        ]

        with patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler"
        ) as mock_crawler_class:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.markdown = MagicMock()
            mock_result.markdown.raw_markdown = "Test content"
            mock_result.metadata = {"title": "Test Article"}
            mock_result.cleaned_html = "<html><body>Test content</body></html>"

            crawler = _build_crawler(mock_result)
            mock_crawler_class.return_value = crawler

            for url in test_cases:
                mock_result.url = url
                result = strategy.extract_data("dummy", url)
                assert result["source"] == urlparse(url).netloc, f"Failed for URL: {url}"
