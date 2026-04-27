from app.models.scraper_runs import ScraperStats
from app.scraping.runner import ScraperRunner


class _DummyScraper:
    KEY = "hackernews"
    DISPLAY_NAME = "HackerNews"

    def __init__(self) -> None:
        self.name = "Hacker News"
        self.ran = False

    def run_with_stats(self) -> ScraperStats:
        self.ran = True
        return ScraperStats(scraped=1, saved=1)


def test_run_scraper_accepts_legacy_name_without_space() -> None:
    scraper = _DummyScraper()
    runner = ScraperRunner.__new__(ScraperRunner)
    runner.scrapers = [scraper]

    stats = runner.run_scraper_with_stats("HackerNews")

    assert stats is not None
    assert stats.saved == 1
    assert scraper.ran is True


def test_run_scraper_accepts_canonical_aggregator_key() -> None:
    scraper = _DummyScraper()
    runner = ScraperRunner.__new__(ScraperRunner)
    runner.scrapers = [scraper]

    stats = runner.run_scraper_with_stats("hackernews")

    assert stats is not None
    assert stats.scraped == 1
    assert scraper.ran is True
