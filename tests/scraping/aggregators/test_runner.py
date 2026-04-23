"""Smoke test for ``load_aggregator_scrapers`` against the real YAML config."""

from __future__ import annotations

from app.scraping.aggregators import (
    AggregatorScraper,
    BrutalistReportAggregatorScraper,
    FinUrlsAggregatorScraper,
    HackerNewsAggregatorScraper,
    MediagazerAggregatorScraper,
    MemeorandumAggregatorScraper,
    SciUrlsAggregatorScraper,
    TechmemeAggregatorScraper,
    known_aggregator_keys,
    load_aggregator_scrapers,
)
from app.scraping.aggregators.config import (
    DEFAULT_AGGREGATORS_CONFIG_PATH,
    HtmlTopicAggregator,
    load_aggregators_config,
)

EXPECTED_KEYS = {
    "hackernews",
    "techmeme",
    "mediagazer",
    "memeorandum",
    "sciurls",
    "finurls",
    "brutalist",
}


def test_known_aggregator_keys_matches_registry() -> None:
    assert known_aggregator_keys() == EXPECTED_KEYS


def test_default_aggregators_yaml_loads_all_scrapers() -> None:
    scrapers = load_aggregator_scrapers()

    assert {s.KEY for s in scrapers} == EXPECTED_KEYS

    by_key: dict[str, AggregatorScraper] = {s.KEY: s for s in scrapers}
    assert isinstance(by_key["hackernews"], HackerNewsAggregatorScraper)
    assert isinstance(by_key["techmeme"], TechmemeAggregatorScraper)
    assert isinstance(by_key["mediagazer"], MediagazerAggregatorScraper)
    assert isinstance(by_key["memeorandum"], MemeorandumAggregatorScraper)
    assert isinstance(by_key["sciurls"], SciUrlsAggregatorScraper)
    assert isinstance(by_key["finurls"], FinUrlsAggregatorScraper)
    assert isinstance(by_key["brutalist"], BrutalistReportAggregatorScraper)

    # Every scraper exposes its KEY/DISPLAY_NAME defaults and adopts the
    # YAML-provided name (which may differ, e.g. "Hacker News" vs. KEY default).
    for scraper in scrapers:
        assert scraper.DISPLAY_NAME
        assert scraper.name


def test_brutalist_yaml_entry_has_expected_topics() -> None:
    config_file = load_aggregators_config(DEFAULT_AGGREGATORS_CONFIG_PATH)
    brutalist = next(entry for entry in config_file.aggregators if entry.key == "brutalist")
    assert isinstance(brutalist, HtmlTopicAggregator)
    assert set(brutalist.topics) == {"science", "business", "politics", "sports"}


def test_disabled_entries_are_skipped(tmp_path) -> None:
    config_path = tmp_path / "aggregators.yml"
    config_path.write_text(
        """
aggregators:
  - key: hackernews
    name: Hacker News
    kind: hackernews
    enabled: false
  - key: sciurls
    name: SciURLs
    kind: html_grouped
    url: https://sciurls.com
    limit: 10
"""
    )
    scrapers = load_aggregator_scrapers(config_path)
    assert {s.KEY for s in scrapers} == {"sciurls"}
