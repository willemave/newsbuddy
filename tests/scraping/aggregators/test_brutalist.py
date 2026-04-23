from app.scraping.aggregators.brutalist import BrutalistReportAggregatorScraper
from app.scraping.aggregators.config import HtmlTopicAggregator

from .conftest import load_fixture


def _settings(topics: list[str] | None = None) -> HtmlTopicAggregator:
    return HtmlTopicAggregator(
        key="brutalist",
        name="Brutalist Report",
        kind="html_topic",
        base_url="https://brutalist.report/topic/{topic}?limit={limit}&hours={hours}",
        topics=topics or ["science"],
        limit=25,
        hours=24,
    )


def test_brutalist_parses_topic_page_and_tags_topic() -> None:
    scraper = BrutalistReportAggregatorScraper(_settings(["science"]))
    items = scraper.parse(load_fixture("brutalist", "science.html"), topic="science")

    # 5 valid items (the brutalist.report/topic/science permalink is filtered).
    assert len(items) == 5
    sources = {item["metadata"]["aggregator"]["metadata"]["source_name"] for item in items}
    assert sources == {"Nature", "Science Magazine", "Phys.org"}

    for item in items:
        metadata = item["metadata"]
        assert metadata["platform"] == "brutalist"
        assert metadata["aggregator"]["key"] == "brutalist"
        assert metadata["aggregator"]["name"] == "Brutalist Report"
        assert metadata["aggregator"]["topic"] == "science"
        assert "brutalist.report" not in item["url"]


def test_brutalist_respects_limit() -> None:
    scraper = BrutalistReportAggregatorScraper(
        HtmlTopicAggregator(
            key="brutalist",
            name="Brutalist Report",
            kind="html_topic",
            base_url="https://brutalist.report/topic/{topic}?limit={limit}&hours={hours}",
            topics=["science"],
            limit=2,
            hours=24,
        )
    )
    items = scraper.parse(load_fixture("brutalist", "science.html"), topic="science")
    assert len(items) == 2
