"""Tests for Atom feed scraper."""

import logging
from pathlib import Path

from app.scraping.atom_unified import AtomScraper, load_atom_feeds


def test_load_atom_feeds_missing_file(tmp_path: Path):
    """Test loading from non-existent config file."""
    config_path = tmp_path / "nonexistent.yml"
    feeds = load_atom_feeds(config_path)
    assert feeds == []


def test_load_atom_feeds_valid_config(tmp_path: Path):
    """Test loading valid Atom feed configuration."""
    config_path = tmp_path / "atom.yml"
    config_content = """
feeds:
  - url: "https://example.com/feed.atom"
    name: "Example Feed"
    limit: 5
  - url: "https://test.com/atom.xml"
    name: "Test Feed"
    limit: 10
"""
    config_path.write_text(config_content)

    feeds = load_atom_feeds(config_path)

    assert len(feeds) == 2
    assert feeds[0]["url"] == "https://example.com/feed.atom"
    assert feeds[0]["name"] == "Example Feed"
    assert feeds[0]["limit"] == 5
    assert feeds[1]["url"] == "https://test.com/atom.xml"
    assert feeds[1]["name"] == "Test Feed"
    assert feeds[1]["limit"] == 10


def test_load_atom_feeds_string_format(tmp_path: Path):
    """Test loading with simple string URL format."""
    config_path = tmp_path / "atom.yml"
    config_content = """
feeds:
  - "https://example.com/feed.atom"
"""
    config_path.write_text(config_content)

    feeds = load_atom_feeds(config_path)

    assert len(feeds) == 1
    assert feeds[0]["url"] == "https://example.com/feed.atom"
    assert feeds[0]["name"] == "Unknown Atom"
    assert feeds[0]["limit"] == 10


def test_atom_scraper_in_runner():
    """Test that AtomScraper is registered in ScraperRunner."""
    from app.scraping.runner import ScraperRunner

    runner = ScraperRunner()
    scraper_names = runner.list_scrapers()

    assert "Atom" in scraper_names


def test_atom_scraper_no_feeds_logs_info(
    monkeypatch,
    caplog,
):
    """Expected empty Atom config should not warn."""
    scraper = AtomScraper()
    monkeypatch.setattr(scraper, "_load_feeds", lambda: [])

    caplog.set_level(logging.INFO)
    items = scraper.scrape()

    assert items == []
    assert any(
        record.levelno == logging.INFO and "No Atom feeds configured" in record.message
        for record in caplog.records
    )
    assert not any(
        "No Atom feeds configured" in record.message and record.levelno >= logging.WARNING
        for record in caplog.records
    )
