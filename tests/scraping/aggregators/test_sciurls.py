from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.scraping.aggregators import _html_grouped
from app.scraping.aggregators.config import HtmlGroupedAggregator
from app.scraping.aggregators.sciurls import SciUrlsAggregatorScraper

from .conftest import load_fixture


def test_sciurls_parses_grouped_blocks() -> None:
    settings = HtmlGroupedAggregator(
        key="sciurls",
        name="SciURLs",
        kind="html_grouped",
        url="https://sciurls.com",
        limit=50,
    )
    scraper = SciUrlsAggregatorScraper(settings)
    html = load_fixture("sciurls", "sample.html")

    items = scraper.parse(html)

    # Two source blocks have external articles; the third block is a category
    # navigation block and should be filtered out by the in-site domain check.
    assert len(items) == 4
    assert {item["metadata"]["aggregator"]["metadata"]["source_name"] for item in items} == {
        "Nature",
        "Science",
    }

    first = items[0]
    assert first["url"].startswith("https://www.nature.com/")
    assert first["title"] == "New telescope spots oldest known galaxy"
    metadata = first["metadata"]
    assert metadata["platform"] == "sciurls"
    assert metadata["source"] == "Nature"
    assert metadata["aggregator"]["key"] == "sciurls"
    assert metadata["aggregator"]["name"] == "SciURLs"
    assert metadata["aggregator"]["metadata"]["source_url"] == "https://www.nature.com"


def test_sciurls_dedupes_repeated_urls() -> None:
    settings = HtmlGroupedAggregator(
        key="sciurls", name="SciURLs", kind="html_grouped", url="https://sciurls.com", limit=50
    )
    html = """
    <div class="publisher-block">
      <div class="publisher-header">
        <a class="icon-container" href="https://nature.com"></a>
        <div class="publisher-text"><span class="title"><span class="primary">
          Nature
        </span></span></div>
      </div>
      <div class="publisher-link">
        <a class="article-link" href="https://nature.com/article-1">Article 1</a>
      </div>
      <div class="publisher-link">
        <a class="article-link" href="https://nature.com/article-1">Article 1 again</a>
      </div>
    </div>
    """
    items = SciUrlsAggregatorScraper(settings).parse(html)
    assert len(items) == 1
    assert items[0]["url"] == "https://nature.com/article-1"


def test_sciurls_parses_current_publisher_card_markup() -> None:
    settings = HtmlGroupedAggregator(
        key="sciurls", name="SciURLs", kind="html_grouped", url="https://sciurls.com", limit=50
    )
    html = """
    <div class="publisher-block" data-publisher="nature">
      <div class="publisher-header">
        <a class="icon-container" href="https://nature.com"></a>
        <div class="publisher-text">
          <span class="title"><span class="primary">Nature</span></span>
        </div>
      </div>
      <div class="publisher-links">
        <div class="publisher-link"><a class="article-link"
          href="https://nature.com/current-story">Current story</a></div>
      </div>
    </div>
    """

    items = SciUrlsAggregatorScraper(settings).parse(html)

    assert len(items) == 1
    assert items[0]["title"] == "Current story"
    aggregator_metadata = items[0]["metadata"]["aggregator"]["metadata"]
    assert aggregator_metadata == {
        "source_name": "Nature",
        "source_url": "https://nature.com",
    }


def test_sciurls_respects_limit() -> None:
    settings = HtmlGroupedAggregator(
        key="sciurls", name="SciURLs", kind="html_grouped", url="https://sciurls.com", limit=2
    )
    html = load_fixture("sciurls", "sample.html")
    items = SciUrlsAggregatorScraper(settings).parse(html)
    assert len(items) == 2


def test_sciurls_fetches_homepage_through_feed_research_runtime(monkeypatch) -> None:
    settings = HtmlGroupedAggregator(
        key="sciurls", name="SciURLs", kind="html_grouped", url="https://sciurls.com", limit=2
    )
    calls: list[str] = []

    class FakeHttpService:
        def fetch(self, url: str, *, headers):  # noqa: ANN001
            del headers
            calls.append(url)
            return SimpleNamespace(text=load_fixture("sciurls", "sample.html"))

    @contextmanager
    def fake_runtime(**kwargs):
        assert kwargs == {"user_id": 0}
        yield FakeHttpService()

    monkeypatch.setattr(_html_grouped, "sandboxed_http_service", fake_runtime)

    items = SciUrlsAggregatorScraper(settings).scrape()

    assert len(items) == 2
    assert calls == ["https://sciurls.com"]


def test_sciurls_sandbox_outage_is_reported_in_scraper_stats(monkeypatch) -> None:
    settings = HtmlGroupedAggregator(
        key="sciurls", name="SciURLs", kind="html_grouped", url="https://sciurls.com", limit=2
    )

    broken_runtime = MagicMock()
    broken_runtime.return_value.__enter__.side_effect = RuntimeError("E2B unavailable")

    monkeypatch.setattr(_html_grouped, "sandboxed_http_service", broken_runtime)

    stats = SciUrlsAggregatorScraper(settings).run_with_stats()

    assert stats.scraped == 0
    assert stats.saved == 0
    assert stats.errors == 1
    assert stats.error_details == ["E2B unavailable"]


def test_sciurls_markup_drift_is_reported_in_scraper_stats(monkeypatch) -> None:
    settings = HtmlGroupedAggregator(
        key="sciurls", name="SciURLs", kind="html_grouped", url="https://sciurls.com", limit=2
    )

    class FakeHttpService:
        def fetch(self, _url: str, *, headers):  # noqa: ANN001
            del headers
            return SimpleNamespace(text="<html><body>unexpected markup</body></html>")

    @contextmanager
    def fake_runtime(**_kwargs):
        yield FakeHttpService()

    monkeypatch.setattr(_html_grouped, "sandboxed_http_service", fake_runtime)

    stats = SciUrlsAggregatorScraper(settings).run_with_stats()

    assert stats.scraped == 0
    assert stats.saved == 0
    assert stats.errors == 1
    assert stats.error_details == ["SciURLs returned no parseable items"]
