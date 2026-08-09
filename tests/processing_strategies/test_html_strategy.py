import threading
import time
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx  # For creating mock Headers
import pytest

from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies import crawl4ai_manager as crawler_manager_module
from app.processing_strategies import html_strategy as html_strategy_module
from app.processing_strategies.html_strategy import HtmlProcessorStrategy
from app.services.firecrawl_client import FirecrawlScrapeResult, FirecrawlUnavailableError

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


@pytest.fixture(autouse=True)
def disable_firecrawl_network(monkeypatch):
    """Keep strategy unit tests from spending Firecrawl credits by default."""

    def _raise_unavailable(*args: Any, **kwargs: Any) -> None:
        raise FirecrawlUnavailableError("Firecrawl disabled in unit tests")

    monkeypatch.setattr(
        "app.processing_strategies.html_strategy.scrape_url_with_firecrawl",
        _raise_unavailable,
    )


@pytest.fixture(autouse=True)
def reset_reusable_crawler():
    """Keep reusable crawl4ai browser state isolated between tests."""
    html_strategy_module._close_reusable_crawler_for_tests()
    yield
    html_strategy_module._close_reusable_crawler_for_tests()


def test_detect_source(html_strategy: HtmlProcessorStrategy):
    """Test source detection from URLs."""
    assert html_strategy._detect_source("https://pubmed.ncbi.nlm.nih.gov/12345") == "PubMed"
    assert (
        html_strategy._detect_source("https://pmc.ncbi.nlm.nih.gov/articles/PMC12345") == "PubMed"
    )
    assert html_strategy._detect_source("https://arxiv.org/abs/1234.5678") == "Arxiv"
    assert html_strategy._detect_source("https://arxiv.org/pdf/1234.5678") == "Arxiv"
    assert html_strategy._detect_source("https://arxiv.org.evil.test/abs/1234.5678") == "web"
    assert html_strategy._detect_source("https://substack.com.evil.test/post") == "web"
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
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
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
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
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
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
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
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
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
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
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
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
    ):
        extracted_data = html_strategy.extract_data("", url)

        failure_message = extracted_data["text_content"]
        assert "Timed out while waiting for page" in failure_message
        assert "status_code=504" in failure_message
        assert "redirected.example.com" in failure_message


def test_extract_data_uses_firecrawl_fallback_when_crawl_returns_empty_body(
    html_strategy: HtmlProcessorStrategy,
):
    """Empty crawl bodies should fall back to Firecrawl before failing extraction."""
    url = "https://giftarticle.ft.com/giftarticle/actions/redeem/example"

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {
        "title": (
            "Q&A with a16z partner Martin Cascado, who leads an AI investment team "
            "(George Hammond/Financial Times)"
        )
    }
    mock_result.url = url
    mock_result.cleaned_html = (
        "<html><head><title>FT gift article</title></head><body></body></html>"
    )

    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = ""
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with (
        patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
        ),
        patch(
            "app.processing_strategies.html_strategy.scrape_url_with_firecrawl",
            return_value=FirecrawlScrapeResult(
                url=url,
                source_url=url,
                title="Recovered gift article title",
                markdown="Recovered article body from Firecrawl fallback.",
                published_time="2026-04-16T12:00:00Z",
            ),
        ),
    ):
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["title"] == "Recovered gift article title"
    assert extracted_data["text_content"] == "Recovered article body from Firecrawl fallback."
    assert extracted_data["used_firecrawl_fallback"] is True
    assert extracted_data["firecrawl_fallback_length"] == 47
    assert extracted_data["extraction_error"] is None


def test_extract_data_recovers_gate_page_with_firecrawl(
    html_strategy: HtmlProcessorStrategy,
):
    """Gate-page crawl results should recover via Firecrawl inside the HTML strategy."""
    url = "https://www.latent.space/p/ainews-anthropics-agent-autonomy"
    gate_text = (
        "This site requires JavaScript to run correctly. "
        "Please turn on JavaScript or unblock scripts."
    )

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "Latent Space"}
    mock_result.url = url
    mock_result.cleaned_html = (
        "<html><body><div class='challenge-error-text'>"
        "This site requires JavaScript to run correctly."
        "</div></body></html>"
    )

    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = gate_text
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with (
        patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
        ),
        patch(
            "app.processing_strategies.html_strategy.scrape_url_with_firecrawl",
            return_value=FirecrawlScrapeResult(
                url=url,
                source_url=url,
                title="Latent Space",
                markdown="Firecrawl recovered the full article body about agent autonomy.",
            ),
        ),
    ):
        extracted_data = html_strategy.extract_data(
            "",
            url,
            context={"content_id": 42, "existing_metadata": {}},
        )

    assert extracted_data["used_firecrawl_fallback"] is True
    assert extracted_data["extraction_error"] is None
    assert "firecrawl recovered the full article body" in extracted_data["text_content"].lower()


def test_extract_data_rejects_malformed_firecrawl_fallback(
    html_strategy: HtmlProcessorStrategy,
):
    """Firecrawl paywall/challenge output should not be treated as recovered content."""
    url = "https://www.ft.com/content/example"
    gate_text = (
        "This site requires JavaScript to run correctly. "
        "Please turn on JavaScript or unblock scripts."
    )

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "Latent Space"}
    mock_result.url = url
    mock_result.cleaned_html = (
        "<html><body><div class='challenge-error-text'>"
        "This site requires JavaScript to run correctly."
        "</div></body></html>"
    )

    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = gate_text
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with (
        patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
        ),
        patch(
            "app.processing_strategies.html_strategy.scrape_url_with_firecrawl",
            return_value=FirecrawlScrapeResult(
                url=url,
                source_url=url,
                title="Subscribe to read",
                markdown="Subscribe to read this article. Sign in to continue reading.",
            ),
        ),
    ):
        extracted_data = html_strategy.extract_data(
            "",
            url,
            context={"content_id": 42, "existing_metadata": {}},
        )

    assert extracted_data["extraction_error"] == "access gate detected: challenge/JS wall content"
    assert extracted_data.get("used_firecrawl_fallback") is None


def test_extract_data_prefers_readability_text_for_chrome_heavy_crawl(
    html_strategy: HtmlProcessorStrategy,
):
    """Successful crawls should still strip page chrome before persistence."""
    url = "https://www.reuters.com/example-story"
    crawl_text = (
        "[Skip to main content](https://www.reuters.com/example-story#main-content) "
        "[Exclusive news, data and analytics](https://www.reuters.com/differentiator/) "
        + " ".join(f"[Related story {i}](https://www.reuters.com/related-{i})" for i in range(40))
        + " LOS ANGELES, May 12 (Reuters) - The real article body starts here."
    )
    readable_text = (
        "LOS ANGELES, May 12 (Reuters) - The real article body starts here. "
        "Regulators approved the spectrum transfer after reviewing public interest "
        "conditions, competition concerns, and deployment requirements. "
    ) * 4

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "Reuters example story"}
    mock_result.url = url
    mock_result.cleaned_html = "<html><body><article>Readable article body</article></body></html>"

    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = crawl_text
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with (
        patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
        ),
        patch(
            "app.processing_strategies.html_strategy.trafilatura.extract",
            return_value=readable_text,
        ),
    ):
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["text_content"] == readable_text.strip()
    assert extracted_data["used_readability_extraction"] is True
    assert extracted_data["extraction_error"] is None


def test_trim_publisher_chrome_cleans_reuters_article_body() -> None:
    raw_text = (
        "[Skip to main content](https://www.reuters.com/story#main-content) "
        "![Image](https://example.com/image.jpg) Caption. "
        "BRUSSELS, May 12 (Reuters) - Meta offered rival AI chatbots free access "
        "to WhatsApp while discussing commitments with EU regulators. "
        "Make sense of the latest ESG trends affecting companies and governments "
        "with the Reuters Sustainable Switch newsletter. Sign up here. "
        "Advertisement · Scroll to continue "
        "The EU antitrust enforcer welcomed the move. "
        "Reporting by Foo Yun Chee; Editing by Nia Williams "
        "Our Standards: The Thomson Reuters Trust Principles. ## Read Next"
    )

    cleaned = HtmlProcessorStrategy._trim_publisher_chrome(
        "https://www.reuters.com/sustainability/example",
        raw_text,
    )

    assert cleaned.startswith("BRUSSELS, May 12 (Reuters) -")
    assert "newsletter" not in cleaned
    assert "Advertisement" not in cleaned
    assert "Reporting by" not in cleaned
    assert "Our Standards" not in cleaned
    assert "Read Next" not in cleaned
    assert "The EU antitrust enforcer welcomed the move." in cleaned


def test_trim_publisher_chrome_cleans_wsj_article_body() -> None:
    raw_text = (
        "Skip to Main Content Select - What to Read Next DJIA 49760.56 "
        "Advertisement This copy is for your personal, non-commercial use only. "
        "# SAP Launches Unified AI, Automation Suite ## German group seeks to stay "
        "on top of a technology that cast doubt on software industry pricing. "
        "The product brings together data, AI agents, and workflow automation. "
        "Copyright ©2026 Dow Jones & Company, Inc. All Rights Reserved. "
        "### Further Reading ### SAP Shares Climb on Cloud Business Resilience"
    )

    cleaned = HtmlProcessorStrategy._trim_publisher_chrome(
        "https://www.wsj.com/tech/ai/example",
        raw_text,
    )

    assert cleaned.startswith("# SAP Launches Unified AI")
    assert "Skip to Main Content" not in cleaned
    assert "This copy is for your personal" not in cleaned
    assert "Copyright ©2026" not in cleaned
    assert "Further Reading" not in cleaned
    assert "workflow automation" in cleaned


def test_extract_data_uses_direct_readability_for_espn_chrome_heavy_crawl(
    html_strategy: HtmlProcessorStrategy,
):
    """Known public pages can recover clean article text when browser markdown is nav-heavy."""
    url = "https://www.espn.com/mlb/story/_/id/1/example-story"
    crawl_text = (
        "Skip to main content Skip to navigation Top Events NBA NHL PGA Tour MLB WNBA "
        + " ".join(f"[League {i}]({url}#)" for i in range(40))
        + " ST. LOUIS -- The real article body appears after a large ESPN nav block."
    )
    direct_readable_text = (
        "ST. LOUIS -- The real article body appears after a large ESPN nav block. "
        "Refsnyder successfully challenged a third strike and then hit a go-ahead "
        "home run in the ninth inning. "
    ) * 4

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "ESPN example story"}
    mock_result.url = url
    mock_result.cleaned_html = "<html><body>browser-cleaned chrome</body></html>"

    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = crawl_text
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    direct_response = httpx.Response(
        200,
        text="<html><body><article>Direct article body</article></body></html>",
        request=httpx.Request("GET", url),
    )
    cast(Any, html_strategy.http_client.get).return_value = direct_response

    with (
        patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
        ),
        patch(
            "app.processing_strategies.html_strategy.trafilatura.extract",
            side_effect=[crawl_text, direct_readable_text],
        ),
    ):
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["text_content"] == direct_readable_text.strip()
    assert extracted_data["used_readability_extraction"] is True
    assert extracted_data["used_direct_readability_extraction"] is True
    cast(Any, html_strategy.http_client.get).assert_called_once_with(
        url,
        headers={"User-Agent": "Mozilla/5.0 NewslyBot/1.0"},
        timeout=20.0,
    )


def test_extract_data_retries_direct_readability_header_candidates(
    html_strategy: HtmlProcessorStrategy,
):
    """Public direct fetch should try the next header profile after a blocked response."""
    url = "https://techcrunch.com/2026/05/12/example-story"
    crawl_text = (
        "Latest Startups Venture Apple Security AI Apps Events Podcasts Newsletters "
        "Submit Topics Latest AI " + " ".join(f"[Nav {i}]({url}#)" for i in range(30))
    )
    direct_readable_text = (
        "The actual TechCrunch article starts here and contains enough reporting "
        "to support a grounded short-form news summary."
    ) * 5

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "TechCrunch example story"}
    mock_result.url = url
    mock_result.cleaned_html = "<html><body>browser-cleaned chrome</body></html>"

    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = crawl_text
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    blocked_response = httpx.Response(
        403,
        text="<html><body>blocked</body></html>",
        request=httpx.Request("GET", url),
    )
    blocked_error = httpx.HTTPStatusError(
        "blocked",
        request=blocked_response.request,
        response=blocked_response,
    )
    direct_response = httpx.Response(
        200,
        text="<html><body><article>Direct clean article body</article></body></html>",
        request=httpx.Request("GET", url),
    )
    cast(Any, html_strategy.http_client.get).side_effect = [blocked_error, direct_response]

    with (
        patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
        ),
        patch(
            "app.processing_strategies.html_strategy.trafilatura.extract",
            side_effect=[crawl_text, direct_readable_text],
        ),
    ):
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["text_content"] == direct_readable_text.strip()
    assert extracted_data["used_direct_readability_extraction"] is True
    assert cast(Any, html_strategy.http_client.get).call_count == 2


def test_extract_data_uses_github_readme_for_repo_urls(
    html_strategy: HtmlProcessorStrategy,
):
    """GitHub repo pages should use README content instead of navigation-heavy HTML."""
    url = "https://github.com/example/project"
    readme = "# Project\n\nThis README explains the project without GitHub navigation chrome."
    response = httpx.Response(
        200,
        text=readme,
        request=httpx.Request("GET", "https://api.github.com/repos/example/project/readme"),
    )
    cast(Any, html_strategy.http_client.get).return_value = response

    with patch("app.processing_strategies.crawl4ai_manager.AsyncWebCrawler") as crawler:
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["title"] == "example/project README"
    assert extracted_data["text_content"] == readme
    assert extracted_data["source"] == "github.com"
    assert extracted_data["used_github_readme_extraction"] is True
    crawler.assert_not_called()
    cast(Any, html_strategy.http_client.get).assert_called_once_with(
        "https://api.github.com/repos/example/project/readme",
        headers={
            "Accept": "application/vnd.github.raw",
            "User-Agent": "NewslyArticleFetcher/1.0 (+https://newsly.local)",
        },
        timeout=20.0,
    )


def test_extract_data_prefers_direct_readability_before_subtle_chrome_fallback(
    html_strategy: HtmlProcessorStrategy,
):
    """Allowlisted public pages should not settle for cleaned text that still has chrome."""
    url = "https://fortune.com/2026/05/10/example-story"
    crawl_text = (
        "- [Home](https://fortune.com/) - [Latest](https://fortune.com/section/latest/) "
        + " ".join(f"[Nav {i}]({url}#)" for i in range(30))
        + " The real story starts much later."
    )
    subtle_chrome_text = (
        "Search [](https://fortune.com/) Subscribe for $1 Subscribe for $1 "
        "The real story starts much later and contains useful reporting."
    ) * 4
    direct_readable_text = (
        "The real story starts here with no nav prelude. It contains useful reporting "
        "about the company, market context, and leadership decisions."
    ) * 4

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "Fortune example story"}
    mock_result.url = url
    mock_result.cleaned_html = "<html><body>cleaned but still chrome-heavy</body></html>"

    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = crawl_text
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    direct_response = httpx.Response(
        200,
        text="<html><body><article>Direct clean article body</article></body></html>",
        request=httpx.Request("GET", url),
    )
    cast(Any, html_strategy.http_client.get).return_value = direct_response

    with (
        patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
        ),
        patch(
            "app.processing_strategies.html_strategy.trafilatura.extract",
            side_effect=[subtle_chrome_text, direct_readable_text],
        ),
    ):
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["text_content"] == direct_readable_text.strip()
    assert extracted_data["used_direct_readability_extraction"] is True


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
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    # Simulate the browser close error
    mock_crawler.close = AsyncMock(
        side_effect=Exception("Browser.close: Connection closed while reading from the driver")
    )

    with patch(
        "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
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


def test_extract_data_reuses_crawler_across_retry_attempts(
    html_strategy: HtmlProcessorStrategy,
    monkeypatch,
):
    """Transient crawl retries should not relaunch Chromium for each attempt."""
    url = "https://example.com/retry-once"
    monkeypatch.setattr(
        html_strategy,
        "_get_source_specific_config",
        lambda _source: {"max_crawl_attempts": 2, "crawl_retry_delay_seconds": 0},
    )

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.metadata = {"title": "Retry Article"}
    mock_result.url = url
    mock_result.cleaned_html = "<html>...</html>"
    mock_markdown = MagicMock()
    mock_markdown.raw_markdown = "Retry article body"
    mock_result.markdown = mock_markdown

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(side_effect=[Exception("timeout"), mock_result])
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
    ) as crawler_class:
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["title"] == "Retry Article"
    assert mock_crawler.arun.await_count == 2
    crawler_class.assert_called_once()
    browser_config = crawler_class.call_args.kwargs["config"]
    assert browser_config.max_pages_before_recycle == 1
    mock_crawler.start.assert_awaited_once()
    mock_crawler.close.assert_not_awaited()


def test_extract_data_reuses_crawler_across_extractions(html_strategy: HtmlProcessorStrategy):
    """Separate article crawls should reuse the process-lifetime crawler."""
    first_url = "https://example.com/first"
    second_url = "https://example.com/second"

    def make_result(url: str, title: str):
        result = MagicMock()
        result.success = True
        result.metadata = {"title": title}
        result.url = url
        result.cleaned_html = "<html>...</html>"
        markdown = MagicMock()
        markdown.raw_markdown = f"{title} body"
        result.markdown = markdown
        return result

    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(
        side_effect=[
            make_result(first_url, "First Article"),
            make_result(second_url, "Second Article"),
        ]
    )
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with patch(
        "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
    ) as crawler_class:
        first = html_strategy.extract_data("", first_url)
        second = html_strategy.extract_data("", second_url)

    assert first["title"] == "First Article"
    assert second["title"] == "Second Article"
    assert mock_crawler.arun.await_count == 2
    crawler_class.assert_called_once()
    mock_crawler.start.assert_awaited_once()
    mock_crawler.close.assert_not_awaited()


def test_reusable_crawler_timeout_cancels_and_replaces_crawler():
    """A hung crawl must release its caller and not poison the next crawl."""
    manager = crawler_manager_module.ReusableCrawlerManager()
    browser_config = MagicMock()
    run_config = MagicMock(session_id=None)
    browser_config_key = ("test",)
    cancelled = threading.Event()

    async def hang_forever(**_kwargs: Any) -> None:
        try:
            await crawler_manager_module.asyncio.Event().wait()
        finally:
            cancelled.set()

    recovered_result = MagicMock()
    timed_out_crawler = AsyncMock()
    timed_out_crawler.start = AsyncMock(return_value=timed_out_crawler)
    timed_out_crawler.close = AsyncMock(return_value=None)
    timed_out_crawler.arun = AsyncMock(side_effect=hang_forever)

    recovered_crawler = AsyncMock()
    recovered_crawler.start = AsyncMock(return_value=recovered_crawler)
    recovered_crawler.close = AsyncMock(return_value=None)
    recovered_crawler.arun = AsyncMock(return_value=recovered_result)

    try:
        with patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler",
            side_effect=[timed_out_crawler, recovered_crawler],
        ):
            with pytest.raises(TimeoutError, match="crawl timeout"):
                manager.run(
                    browser_config=browser_config,
                    browser_config_key=browser_config_key,
                    url="https://example.com/hangs",
                    run_config=run_config,
                    timeout_seconds=0.05,
                )

            actual = manager.run(
                browser_config=browser_config,
                browser_config_key=browser_config_key,
                url="https://example.com/recovers",
                run_config=run_config,
                timeout_seconds=1,
            )

        assert actual is recovered_result
        assert cancelled.wait(timeout=0.5)
        timed_out_crawler.close.assert_awaited_once()
        recovered_crawler.start.assert_awaited_once()
    finally:
        manager.close()


def test_reusable_crawler_lock_wait_obeys_crawl_deadline():
    """A caller queued behind another crawl must not wait forever for the lock."""
    manager = crawler_manager_module.ReusableCrawlerManager()
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock() -> None:
        with manager._lock:
            lock_acquired.set()
            release_lock.wait(timeout=1)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert lock_acquired.wait(timeout=0.5)

    started_at = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="waiting for crawler access"):
            manager.run(
                browser_config=MagicMock(),
                browser_config_key=("test",),
                url="https://example.com/queued",
                run_config=MagicMock(),
                timeout_seconds=0.05,
            )
        assert time.monotonic() - started_at < 0.5
    finally:
        release_lock.set()
        holder.join(timeout=0.5)
        manager.close()


@pytest.mark.asyncio
async def test_reusable_crawler_close_is_bounded(monkeypatch):
    """A stuck browser close must clear reusable state within its cleanup bound."""
    manager = crawler_manager_module.ReusableCrawlerManager()

    async def hang_close() -> None:
        await crawler_manager_module.asyncio.Event().wait()

    crawler = AsyncMock()
    crawler.close = AsyncMock(side_effect=hang_close)
    manager._crawler = crawler
    manager._crawler_key = ("test",)
    manager._created_at = crawler_manager_module.time.monotonic()
    monkeypatch.setattr(
        crawler_manager_module,
        "REUSABLE_CRAWLER_ASYNC_CLOSE_TIMEOUT_SECONDS",
        0.01,
    )

    await manager._close_crawler()

    assert manager._crawler is None
    crawler.close.assert_awaited_once()


def test_extract_data_uses_firecrawl_for_discussion_only_extraction(
    html_strategy: HtmlProcessorStrategy,
):
    """Malformed Substack comment-thread payloads should fall back to Firecrawl."""
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
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    with (
        patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
        ),
        patch(
            "app.processing_strategies.html_strategy.scrape_url_with_firecrawl",
            return_value=FirecrawlScrapeResult(
                title="World Models: Computing the Uncomputable",
                url=url,
                source_url=url,
                markdown=(
                    "Full article body\n\n#### Discussion about this post\n"
                    "CommentsRestacks\nThread replies"
                ),
            ),
        ),
    ):
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["title"] == "World Models: Computing the Uncomputable"
    assert extracted_data["text_content"] == "Full article body"
    assert extracted_data["final_url_after_redirects"] == url
    assert extracted_data["extraction_error"] is None
    cast(Any, html_strategy.http_client.get).assert_not_called()


def test_extract_data_fails_when_firecrawl_fallback_is_unusable(
    html_strategy: HtmlProcessorStrategy,
):
    """Raw HTTP parsing should not run after Firecrawl cannot recover useful text."""
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
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    mock_get = cast(Any, html_strategy.http_client.get)

    with (
        patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
        ),
        patch(
            "app.processing_strategies.html_strategy.scrape_url_with_firecrawl",
            return_value=FirecrawlScrapeResult(
                title="World Models: Computing the Uncomputable",
                url=url,
                source_url=url,
                markdown=(
                    "#### Discussion about this post\n"
                    "CommentsRestacks\nThread replies\n"
                    "This site requires JavaScript to run correctly."
                ),
            ),
        ),
    ):
        extracted_data = html_strategy.extract_data("", url)

    assert extracted_data["extraction_error"] == "access gate detected: challenge/JS wall content"
    mock_get.assert_not_called()


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
    mock_crawler.start = AsyncMock(return_value=mock_crawler)
    mock_crawler.close = AsyncMock(return_value=None)

    table_strategy = MagicMock(name="table_strategy")
    run_config_instance = MagicMock(name="run_config")

    with (
        patch(
            "app.processing_strategies.crawl4ai_manager.AsyncWebCrawler", return_value=mock_crawler
        ),
        patch(
            "app.processing_strategies.html_strategy.LLMConfig", return_value=MagicMock()
        ) as llm_config_cls,
        patch(
            "app.processing_strategies.html_strategy.LLMTableExtraction",
            return_value=table_strategy,
        ) as table_extraction_cls,
        patch(
            "app.processing_strategies.html_strategy.CrawlerRunConfig",
            return_value=run_config_instance,
        ) as run_config_cls,
    ):
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
