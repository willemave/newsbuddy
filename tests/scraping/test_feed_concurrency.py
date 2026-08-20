from __future__ import annotations

import threading
import time
from inspect import signature

import pytest

from app.scraping.atom_unified import AtomScraper
from app.scraping.feed_concurrency import run_feed_jobs
from app.scraping.podcast_unified import PodcastUnifiedScraper
from app.scraping.substack_unified import SubstackScraper

type FeedScraperType = type[AtomScraper] | type[SubstackScraper] | type[PodcastUnifiedScraper]


@pytest.mark.parametrize("scraper_type", [AtomScraper, SubstackScraper, PodcastUnifiedScraper])
def test_feed_scrapers_expose_one_public_scrape_mode(scraper_type: FeedScraperType) -> None:
    assert tuple(signature(scraper_type.scrape).parameters) == ("self",)


@pytest.mark.parametrize(
    ("scraper_type", "load_attr"),
    [
        (AtomScraper, "_load_feeds"),
        (SubstackScraper, "_load_feeds"),
        (PodcastUnifiedScraper, "_load_podcast_feeds"),
    ],
)
def test_feed_http_failure_is_reported_in_scraper_stats(
    scraper_type: FeedScraperType,
    load_attr: str,
    monkeypatch,
) -> None:
    scraper = scraper_type()
    feed_url = "https://publisher.example/feed.xml"
    monkeypatch.setattr(
        scraper,
        load_attr,
        lambda: [{"url": feed_url, "name": "Publisher", "limit": 10}],
    )

    def fail_fetch(*_args, **_kwargs):
        raise RuntimeError("Feed HTTP unavailable")

    monkeypatch.setattr(
        f"{scraper_type.__module__}.fetch_and_parse_feed",
        fail_fetch,
    )

    stats = scraper.run_with_stats()

    assert (stats.scraped, stats.saved, stats.duplicates, stats.errors) == (0, 0, 0, 1)
    assert stats.error_details == [f"{feed_url}: Feed HTTP unavailable"]


def test_run_feed_jobs_is_bounded_ordered_and_failure_isolated() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    def scrape_feed(feed: int) -> list[int]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.02)
            if feed == 2:
                raise RuntimeError("broken feed")
            return [feed]
        finally:
            with lock:
                active -= 1

    failures: list[tuple[int, Exception]] = []
    results = run_feed_jobs(
        list(range(6)),
        scrape_feed,
        max_workers=3,
        on_error=lambda feed, error: failures.append((feed, error)),
    )

    assert results == [0, 1, 3, 4, 5]
    assert peak == 3
    assert len(failures) == 1
    assert failures[0][0] == 2
    assert str(failures[0][1]) == "broken feed"
