from app.scraping.aggregators.config import HtmlGroupedAggregator
from app.scraping.aggregators.finurls import FinUrlsAggregatorScraper

from .conftest import load_fixture


def test_finurls_parses_grouped_blocks() -> None:
    settings = HtmlGroupedAggregator(
        key="finurls",
        name="FinURLs",
        kind="html_grouped",
        url="https://finurls.com",
        limit=50,
    )
    items = FinUrlsAggregatorScraper(settings).parse(load_fixture("finurls", "sample.html"))

    assert len(items) == 3
    sources = {item["metadata"]["aggregator"]["metadata"]["source_name"] for item in items}
    assert sources == {"Bloomberg", "Financial Times"}

    first = items[0]
    metadata = first["metadata"]
    assert metadata["platform"] == "finurls"
    assert metadata["aggregator"]["key"] == "finurls"
    assert metadata["aggregator"]["name"] == "FinURLs"
    assert metadata["article"]["source_domain"] == "bloomberg.com"
    assert first["url"].startswith("https://www.bloomberg.com")
