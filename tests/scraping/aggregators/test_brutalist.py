from types import SimpleNamespace

from app.scraping.aggregators import brutalist
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


def test_brutalist_reuses_pipeline_http_client_across_topics(monkeypatch) -> None:
    client_requests = 0
    fetched_urls: list[str] = []

    class FakeHttpService:
        def fetch_bounded_public(self, url: str, *, headers):  # noqa: ANN001
            nonlocal client_requests
            client_requests += 1
            del headers
            fetched_urls.append(url)
            return SimpleNamespace(text=load_fixture("brutalist", "science.html"))

    http_service = FakeHttpService()
    monkeypatch.setattr(brutalist, "get_http_service", lambda: http_service)

    items = BrutalistReportAggregatorScraper(_settings(["science", "technology"])).scrape()

    assert items
    assert client_requests == 2
    assert len(fetched_urls) == 2
