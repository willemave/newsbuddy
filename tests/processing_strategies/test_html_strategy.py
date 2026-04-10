from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx  # For creating mock Headers
import pytest

from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.html_strategy import HtmlProcessorStrategy
from app.services.exa_client import ExaContentResult

SAMPLE_HTML_CONTENT = """
<html>
<head><title>Test Article Title</title></head>
<body>
    <h1>Main Heading</h1>
    <p>This is the main content of the article. It's very informative.</p>
    <p>Author: John Doe</p>
    <p>Date: 2023-01-15</p>
    <a href="/related_link">Related</a>
</body>
</html>
"""

SAMPLE_ARXIV_HTML_CONTENT = """
<html><head><title>ArXiv Page</title></head><body>Abstract page, not PDF.</body></html>
"""

SAMPLE_EXTRACTED_MARKDOWN = """
# Test Article Title

This is the main content of the article. It's very informative.

Author: John Doe
Date: 2023-01-15
"""


@pytest.fixture
def mock_http_client():
    """Fixture to mock RobustHttpClient."""
    return MagicMock(spec=RobustHttpClient)


@pytest.fixture
def html_strategy(mock_http_client):
    """Fixture to provide an instance of HtmlProcessorStrategy with a mocked http_client."""
    return HtmlProcessorStrategy(http_client=mock_http_client)


def test_detect_source(html_strategy: HtmlProcessorStrategy):
    """Test source detection from URLs."""
    assert html_strategy._detect_source("https://pubmed.ncbi.nlm.nih.gov/12345") == "PubMed"
    assert (
        html_strategy._detect_source("https://pmc.ncbi.nlm.nih.gov/articles/PMC12345") == "PubMed"
    )
    assert html_strategy._detect_source("https://arxiv.org/abs/1234.5678") == "Arxiv"
    assert html_strategy._detect_source("https://arxiv.org/pdf/1234.5678") == "Arxiv"
    assert html_strategy._detect_source("https://example.com/article") == "web"


def test_preprocess_url_pubmed(html_strategy: HtmlProcessorStrategy):
    """Test PubMed URL preprocessing to PMC."""
    pubmed_url = "https://pubmed.ncbi.nlm.nih.gov/12345"
    expected_pmc_url = "https://pmc.ncbi.nlm.nih.gov/articles/pmid/12345/"
    processed_url = html_strategy.preprocess_url(pubmed_url)
    assert processed_url == expected_pmc_url


def test_preprocess_url_arxiv(html_strategy: HtmlProcessorStrategy):
    """Test arXiv URL preprocessing."""
    arxiv_abs_url = "https://arxiv.org/abs/1234.5678"
    expected_pdf_url = "https://arxiv.org/pdf/1234.5678"
    processed_url = html_strategy.preprocess_url(arxiv_abs_url)
    assert processed_url == expected_pdf_url

    non_arxiv_url = "http://example.com/page.html"
    processed_non_arxiv_url = html_strategy.preprocess_url(non_arxiv_url)
    assert processed_non_arxiv_url == non_arxiv_url


def test_can_handle_url_html_content_type(html_strategy: HtmlProcessorStrategy):
    """Test can_handle_url with 'text/html' content type."""
    headers = httpx.Headers({"Content-Type": "text/html; charset=utf-8"})
    assert html_strategy.can_handle_url("http://example.com", headers) is True


def test_can_handle_url_other_content_type(html_strategy: HtmlProcessorStrategy):
    """Test can_handle_url with non-HTML content type."""
    headers = httpx.Headers({"Content-Type": "application/pdf"})
    assert html_strategy.can_handle_url("http://example.com/doc.pdf", headers) is False


def test_can_handle_url_no_headers_html_pattern(html_strategy: HtmlProcessorStrategy):
    """Test can_handle_url with a typical HTML URL pattern when no headers are provided."""
    assert html_strategy.can_handle_url("http://example.com/article.html", None) is True
    assert (
        html_strategy.can_handle_url("http://example.com/some/path", None) is True
    )  # General path


def test_can_handle_url_no_headers_other_pattern(html_strategy: HtmlProcessorStrategy):
    """Test can_handle_url with non-HTML URL patterns when no headers are provided."""
    assert html_strategy.can_handle_url("http://example.com/doc.pdf", None) is False
    assert html_strategy.can_handle_url("http://example.com/data.xml", None) is False
    # Test that preprocessed arXiv PDF URL is not handled by HTML strategy
    assert html_strategy.can_handle_url("https://arxiv.org/pdf/1234.5678", None) is False


def test_download_content(html_strategy: HtmlProcessorStrategy):
    """Test HTML content download - now just returns URL for crawl4ai."""
    url = "http://example.com/article.html"
    content = html_strategy.download_content(url)
    # In the new implementation, download_content just returns the URL
    assert content == url


# Tests for _extract_with_crawl4ai have been removed as the method no longer exists


def test_extract_data_successful(html_strategy: HtmlProcessorStrategy):
    """Test successful data extraction with crawl4ai."""
    url = "http://example.com/article.html"

    # Mock the crawler and its result
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "Test Article Title"}
    mock_result.url = url
    mock_result.cleaned_html = "<html>...</html>"

    # Create a mock markdown object with raw_markdown attribute
    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = SAMPLE_EXTRACTED_MARKDOWN
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
    mock_crawler.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.html_strategy.AsyncWebCrawler", return_value=mock_crawler
    ):
        extracted_data = html_strategy.extract_data(SAMPLE_HTML_CONTENT, url)

        assert extracted_data["title"] == "Test Article Title"
        assert "John Doe" in extracted_data["text_content"]
        assert extracted_data["content_type"] == "html"
        assert extracted_data["source"] == "example.com"
        assert extracted_data["final_url_after_redirects"] == url


def test_extract_data_with_metadata_extraction(html_strategy: HtmlProcessorStrategy):
    """Test data extraction with metadata parsing."""
    url = "http://example.com/article.html"

    content_with_metadata = """
# Test Article Title

Author: Jane Smith
Published: 2023-12-25

This is the article content.
"""

    # Mock the crawler and its result
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "Test Article Title"}
    mock_result.url = url
    mock_result.cleaned_html = "<html>...</html>"

    # Create a mock markdown object with raw_markdown attribute
    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = content_with_metadata
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
    mock_crawler.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.html_strategy.AsyncWebCrawler", return_value=mock_crawler
    ):
        extracted_data = html_strategy.extract_data("", url)

        assert extracted_data["author"] == "Jane Smith"
        assert extracted_data["publication_date"] is not None
        assert extracted_data["publication_date"].year == 2023
        assert extracted_data["publication_date"].month == 12
        assert extracted_data["publication_date"].day == 25


def test_extract_data_pubmed_source(html_strategy: HtmlProcessorStrategy):
    """Test data extraction for PubMed URLs."""
    url = "https://pmc.ncbi.nlm.nih.gov/articles/pmid/12345/"

    # Mock the crawler and its result
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "PubMed Article"}
    mock_result.url = url
    mock_result.cleaned_html = "<html>...</html>"

    # Create a mock markdown object with raw_markdown attribute
    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = "PubMed article content"
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
    mock_crawler.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.html_strategy.AsyncWebCrawler", return_value=mock_crawler
    ):
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["source"] == "pmc.ncbi.nlm.nih.gov"


def test_extract_data_arxiv_source(html_strategy: HtmlProcessorStrategy):
    """Test data extraction for ArXiv URLs."""
    url = "https://arxiv.org/pdf/1234.5678"

    # Mock the crawler and its result
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "ArXiv Paper"}
    mock_result.url = url
    mock_result.cleaned_html = "<html>...</html>"

    # Create a mock markdown object with raw_markdown attribute
    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = "ArXiv paper content"
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
    mock_crawler.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.html_strategy.AsyncWebCrawler", return_value=mock_crawler
    ):
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["source"] == "arxiv.org"


def test_extract_data_failure(html_strategy: HtmlProcessorStrategy):
    """Test data extraction when crawl4ai fails."""
    url = "http://example.com/article.html"

    # Mock the crawler and its result with failure
    mock_result = MagicMock()
    mock_result.success = False
    mock_result.error = "Network error"

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
    mock_crawler.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.html_strategy.AsyncWebCrawler", return_value=mock_crawler
    ):
        extracted_data = html_strategy.extract_data("", url)

        # Updated to match the new error handling behavior
        assert "Content from" in extracted_data["title"]
        assert "Failed to extract content" in extracted_data["text_content"]
        assert extracted_data["content_type"] == "html"
        assert extracted_data["source"] == "example.com"
        assert "extraction_error" in extracted_data


def test_extract_data_failure_includes_error_message_details(
    html_strategy: HtmlProcessorStrategy,
):
    """Ensure crawl4ai failure surfaces detailed error metadata."""
    url = "http://example.com/article.html"

    mock_result = MagicMock()
    mock_result.success = False
    mock_result.error_message = "Timed out while waiting for page"
    mock_result.status_code = 504
    mock_result.redirected_url = "https://redirected.example.com/article"

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
    mock_crawler.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.html_strategy.AsyncWebCrawler", return_value=mock_crawler
    ):
        extracted_data = html_strategy.extract_data("", url)

        failure_message = extracted_data["text_content"]
        assert "Timed out while waiting for page" in failure_message
        assert "status_code=504" in failure_message
        assert "redirected.example.com" in failure_message


def test_newspaper_fallback_fetch_for_allowlisted_domain(html_strategy: HtmlProcessorStrategy):
    """Domain-specific newspaper fallback should return parsed article data."""
    article = SimpleNamespace(
        title="OpenAI investor says AI requires an income tax overhaul",
        text="A real article body extracted by newspaper.",
        authors=["Financial Times"],
        publish_date=datetime(2026, 3, 29),
        article_html=(
            "<html><head><meta property='og:title' "
            "content='OpenAI investor says AI requires an income tax overhaul'></head></html>"
        ),
    )

    with patch.dict("sys.modules", {"newspaper": SimpleNamespace(article=lambda _url: article)}):
        extracted_data = html_strategy._newspaper_fallback_fetch(  # pylint: disable=protected-access
            "https://www.ft.com/content/example",
        )

    assert extracted_data is not None
    assert extracted_data["title"] == "OpenAI investor says AI requires an income tax overhaul"
    assert extracted_data["text_content"] == "A real article body extracted by newspaper."
    assert extracted_data["author"] == "Financial Times"
    assert extracted_data["used_newspaper_fallback"] is True


def test_newspaper_fallback_skips_non_allowlisted_domain(html_strategy: HtmlProcessorStrategy):
    """Newspaper fallback should only run on the blocked-domain allowlist."""
    with patch.dict("sys.modules", {"newspaper": SimpleNamespace(article=lambda _url: None)}):
        extracted_data = html_strategy._newspaper_fallback_fetch(  # pylint: disable=protected-access
            "https://example.com/article",
        )

    assert extracted_data is None


def test_extract_data_with_browser_close_error(html_strategy: HtmlProcessorStrategy):
    """Test that browser close errors don't fail the extraction."""
    url = "https://en.wikipedia.org/wiki/Pfeilstorch"
    
    # Mock successful extraction result
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "Pfeilstorch Article"}
    mock_result.url = url
    mock_result.cleaned_html = "<html>...</html>"
    
    # Create a mock markdown object
    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = "Article about Pfeilstorch"
    mock_result.markdown = mock_markdown
    
    # Mock crawler that raises error on close
    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
    # Simulate the browser close error
    mock_crawler.__aexit__ = AsyncMock(
        side_effect=Exception("Browser.close: Connection closed while reading from the driver")
    )
    
    with patch(
        "app.processing_strategies.html_strategy.AsyncWebCrawler", return_value=mock_crawler
    ):
        # Should not raise an exception despite browser close error
        extracted_data = html_strategy.extract_data("", url)
        
        # Verify extraction succeeded
        assert extracted_data["title"] == "Pfeilstorch Article"
        assert extracted_data["text_content"] == "Article about Pfeilstorch"
        assert extracted_data["content_type"] == "html"
        assert extracted_data["final_url_after_redirects"] == url
        # Extraction succeeded; error marker should be empty.
        assert extracted_data.get("extraction_error") is None


def test_extract_data_uses_fallback_for_discussion_only_extraction(
    html_strategy: HtmlProcessorStrategy,
):
    """Malformed Substack comment-thread payloads should fall back to Exa first."""
    url = "https://www.notboring.co/p/world-models"

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "World Models: Computing the Uncomputable"}
    mock_result.url = url
    mock_result.cleaned_html = "<html><body>Discussion payload</body></html>"

    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = (
        "#### Discussion about this post\n"
        "CommentsRestacks\n"
        "The Man U thought experiment is a great framing.\n"
        "This site requires JavaScript to run correctly. Please turn on JavaScript."
    )
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
    mock_crawler.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.processing_strategies.html_strategy.AsyncWebCrawler", return_value=mock_crawler),
        patch(
            "app.processing_strategies.html_strategy.exa_get_contents",
            return_value=[
                ExaContentResult(
                    title="World Models: Computing the Uncomputable",
                    url=url,
                    text=(
                        "Full article body\n\n#### Discussion about this post\n"
                        "CommentsRestacks\nThread replies"
                    ),
                )
            ],
        ),
    ):
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["title"] == "World Models: Computing the Uncomputable"
    assert extracted_data["text_content"] == "Full article body"
    assert extracted_data["final_url_after_redirects"] == url
    assert extracted_data["extraction_error"] is None
    html_strategy.http_client.get.assert_not_called()


def test_extract_data_uses_http_fallback_when_exa_fallback_fails(
    html_strategy: HtmlProcessorStrategy,
):
    """HTTP fallback should run only after Exa fallback returns nothing useful."""
    url = "https://www.notboring.co/p/world-models"

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "World Models: Computing the Uncomputable"}
    mock_result.url = url
    mock_result.cleaned_html = "<html><body>Discussion payload</body></html>"

    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = (
        "#### Discussion about this post\n"
        "CommentsRestacks\n"
        "The Man U thought experiment is a great framing.\n"
        "This site requires JavaScript to run correctly. Please turn on JavaScript."
    )
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
    mock_crawler.__aexit__ = AsyncMock(return_value=None)

    mock_response = MagicMock()
    mock_response.text = (
        "<html><head><title>World Models: Computing the Uncomputable</title></head>"
        "<body><article>Full article body</article></body></html>"
    )
    mock_response.url = url
    html_strategy.http_client.get.return_value = mock_response

    with (
        patch("app.processing_strategies.html_strategy.AsyncWebCrawler", return_value=mock_crawler),
        patch("app.processing_strategies.html_strategy.exa_get_contents", return_value=[]),
        patch(
            "app.processing_strategies.html_strategy.trafilatura.extract",
            return_value="Full article body",
        ),
    ):
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["title"] == "World Models: Computing the Uncomputable"
    assert extracted_data["text_content"] == "Full article body"
    assert extracted_data["final_url_after_redirects"] == url
    assert extracted_data["extraction_error"] is None
    html_strategy.http_client.get.assert_called_once()


def test_prepare_for_llm(html_strategy: HtmlProcessorStrategy):
    """Test preparation of extracted data for LLM processing."""
    extracted_data = {
        "title": "Test Article Title",
        "author": "John Doe",
        "publication_date": "2023-01-15",
        "text_content": "This is the main content.",
        "content_type": "html",
        "source": "web",
        "final_url_after_redirects": "http://example.com/article.html",
    }
    llm_input = html_strategy.prepare_for_llm(extracted_data)

    assert llm_input["content_to_filter"] == "This is the main content."
    assert llm_input["content_to_summarize"] == "This is the main content."
    assert llm_input["is_pdf"] is False


def test_extract_internal_urls_placeholder(html_strategy: HtmlProcessorStrategy):
    """Test the placeholder implementation of extract_internal_urls."""
    # As per current implementation, it's a placeholder returning an empty list.
    urls = html_strategy.extract_internal_urls(SAMPLE_HTML_CONTENT, "http://example.com")
    assert urls == []


def test_get_source_specific_config(html_strategy: HtmlProcessorStrategy):
    """Test source-specific configuration generation."""
    # Test web config (default)
    web_config = html_strategy._get_source_specific_config("web")
    assert web_config["word_count_threshold"] == 20
    assert "script" in web_config["excluded_tags"]
    assert web_config["exclude_external_links"] is True

    # Test Substack config
    substack_config = html_strategy._get_source_specific_config("Substack")
    assert "form" in substack_config["excluded_tags"]
    assert ".subscribe-widget" in substack_config["excluded_selector"]
    assert ".post" in substack_config["target_elements"]

    # Test PubMed config
    pubmed_config = html_strategy._get_source_specific_config("PubMed")
    assert pubmed_config["word_count_threshold"] == 10
    assert len(pubmed_config["excluded_tags"]) < len(
        web_config["excluded_tags"]
    )  # Less strict for scientific content

    # Test Arxiv config
    arxiv_config = html_strategy._get_source_specific_config("Arxiv")
    assert arxiv_config.get("pdf") is True


def test_extract_data_includes_table_strategy(monkeypatch, mock_http_client):
    """Ensure table extraction strategy wiring when enabled."""
    url = "http://example.com/tables"

    settings_stub = MagicMock()
    settings_stub.crawl4ai_enable_table_extraction = True
    settings_stub.crawl4ai_table_provider = "openai/gpt-4.1-mini"
    settings_stub.crawl4ai_table_css_selector = None
    settings_stub.crawl4ai_table_enable_chunking = True
    settings_stub.crawl4ai_table_chunk_token_threshold = 3200
    settings_stub.crawl4ai_table_min_rows_per_chunk = 5
    settings_stub.crawl4ai_table_max_parallel_chunks = 2
    settings_stub.crawl4ai_table_verbose = False
    settings_stub.openai_api_key = "sk-test"
    settings_stub.google_api_key = None
    settings_stub.anthropic_api_key = None

    monkeypatch.setattr(
        "app.processing_strategies.html_strategy.get_settings", lambda: settings_stub
    )

    strategy = HtmlProcessorStrategy(http_client=mock_http_client)

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "Table Article"}
    mock_result.url = url
    mock_result.cleaned_html = "<html></html>"
    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = "Content"
    mock_result.markdown = mock_markdown
    mock_table = MagicMock()
    mock_table.markdown = "|A|"
    mock_result.tables = [mock_table]

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
    mock_crawler.__aexit__ = AsyncMock(return_value=None)

    table_strategy = MagicMock(name="table_strategy")
    run_config_instance = MagicMock(name="run_config")

    with patch(
        "app.processing_strategies.html_strategy.AsyncWebCrawler", return_value=mock_crawler
    ), patch(
        "app.processing_strategies.html_strategy.LLMConfig", return_value=MagicMock()
    ) as llm_config_cls, patch(
        "app.processing_strategies.html_strategy.LLMTableExtraction",
        return_value=table_strategy,
    ) as table_extraction_cls, patch(
        "app.processing_strategies.html_strategy.CrawlerRunConfig",
        return_value=run_config_instance,
    ) as run_config_cls:
        extracted_data = strategy.extract_data("", url)

    table_extraction_cls.assert_called_once()
    llm_config_cls.assert_called_once()
    assert run_config_cls.call_args.kwargs["table_extraction"] is table_strategy
    assert extracted_data["table_markdown"] == ["|A|"]


def test_prepare_for_llm_merges_table_markdown(html_strategy: HtmlProcessorStrategy):
    """Verify that extracted tables are appended for LLM consumption."""
    extracted = {
        "text_content": "Base content",
        "table_markdown": ["| A |", "| 1 |"],
        "final_url_after_redirects": "http://example.com",
    }

    prepared = html_strategy.prepare_for_llm(extracted)

    assert "## Extracted Tables" in prepared["content_to_summarize"]
    assert "| A |" in prepared["content_to_summarize"]
