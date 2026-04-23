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
    <div class="box">
      <div class="boxhead"><a href="https://nature.com">Nature</a></div>
      <ul>
        <li><a href="https://nature.com/article-1">Article 1</a></li>
        <li><a href="https://nature.com/article-1">Article 1 again</a></li>
      </ul>
    </div>
    """
    items = SciUrlsAggregatorScraper(settings).parse(html)
    assert len(items) == 1
    assert items[0]["url"] == "https://nature.com/article-1"


def test_sciurls_respects_limit() -> None:
    settings = HtmlGroupedAggregator(
        key="sciurls", name="SciURLs", kind="html_grouped", url="https://sciurls.com", limit=2
    )
    html = load_fixture("sciurls", "sample.html")
    items = SciUrlsAggregatorScraper(settings).parse(html)
    assert len(items) == 2
