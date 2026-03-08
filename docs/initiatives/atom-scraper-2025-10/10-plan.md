# Atom Feed Scraper Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Add an Atom-style feed scraper following the same architecture as the Substack scraper, with YAML configuration and unified scraping patterns.

**Architecture:** Create a new `AtomScraper` class inheriting from `BaseScraper`, load feed URLs from `config/atom.yml`, parse Atom feeds using `feedparser`, and emit standardized items for the content pipeline.

**Tech Stack:** Python 3.12, FastAPI, feedparser, SQLAlchemy 2.x, YAML config

---

## Task 1: Create Atom Feed Configuration File

**Files:**
- Create: `config/atom.yml`
- Create: `config/atom.example.yml`

**Step 1: Write the atom.yml config file**

Create `config/atom.yml` with sample Atom feeds:

```yaml
# Atom Feed URLs
# Format: Standard Atom feed URLs
# Name and description will be extracted from the feed

feeds:
  - url: "https://example.com/feed.atom"
    name: "Example Feed"
    limit: 5
```

**Step 2: Write the atom.example.yml template**

Create `config/atom.example.yml` with the same structure:

```yaml
# Atom Feed URLs
# Format: Standard Atom feed URLs
# Name and description will be extracted from the feed

feeds:
  - url: "https://example.com/feed.atom"
    name: "Example Feed"
    limit: 5
```

**Step 3: Verify config files exist**

Run: `ls -la config/atom*.yml`
Expected: Two files listed (atom.yml and atom.example.yml)

**Step 4: Commit config files**

```bash
git add config/atom.yml config/atom.example.yml
git commit -m "feat: add Atom feed scraper configuration files"
```

---

## Task 2: Create Atom Scraper Module with Tests

**Files:**
- Create: `app/scraping/atom_unified.py`
- Create: `app/tests/test_atom_scraper.py`

**Step 1: Write failing test for config loading**

Create `app/tests/test_atom_scraper.py`:

```python
"""Tests for Atom feed scraper."""

import pytest
from pathlib import Path
from app.scraping.atom_unified import load_atom_feeds


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
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest app/tests/test_atom_scraper.py::test_load_atom_feeds_missing_file -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'app.scraping.atom_unified'"

**Step 3: Write minimal atom_unified.py implementation**

Create `app/scraping/atom_unified.py`:

```python
"""Unified Atom feed scraper following the new architecture."""

import contextlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import feedparser
import yaml

from app.core.logging import get_logger
from app.models.metadata import ContentType
from app.scraping.base import BaseScraper
from app.utils.error_logger import create_error_logger, log_scraper_event
from app.utils.paths import resolve_config_directory, resolve_config_path

ENCODING_OVERRIDE_EXCEPTIONS = tuple(
    exc
    for exc in (
        getattr(feedparser, "CharacterEncodingOverride", None),
        getattr(getattr(feedparser, "exceptions", None), "CharacterEncodingOverride", None),
    )
    if isinstance(exc, type)
)

logger = get_logger(__name__)
_MISSING_CONFIG_WARNINGS: set[str] = set()


def _resolve_atom_config_path(config_path: str | Path | None) -> Path:
    """Resolve the Atom config path."""
    if config_path is None:
        return resolve_config_path("ATOM_CONFIG_PATH", "atom.yml")

    candidate = Path(config_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)

    base_dir = resolve_config_directory()
    return (base_dir / candidate).resolve(strict=False)


def _emit_missing_config_warning(resolved_path: Path) -> None:
    """Emit a warning for missing config file (only once)."""
    key = str(resolved_path.resolve(strict=False))
    if key in _MISSING_CONFIG_WARNINGS:
        return
    _MISSING_CONFIG_WARNINGS.add(key)
    log_scraper_event(
        service="Atom",
        event="config_missing",
        level=logging.WARNING,
        metric="scrape_config_missing",
        path=str(resolved_path.resolve(strict=False)),
    )


def load_atom_feeds(config_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Loads Atom feed URLs, names, and limits from a YAML file."""
    resolved_path = _resolve_atom_config_path(config_path)

    if not resolved_path.exists():
        _emit_missing_config_warning(resolved_path)
        return []

    try:
        with open(resolved_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as exc:
        log_scraper_event(
            service="Atom",
            event="config_load_failed",
            level=logging.ERROR,
            path=str(resolved_path),
            error=str(exc),
        )
        return []

    feeds = config.get("feeds", [])
    result: list[dict[str, Any]] = []
    for feed in feeds:
        if isinstance(feed, dict) and feed.get("url"):
            result.append(
                {
                    "url": feed["url"],
                    "name": feed.get("name", "Unknown Atom"),
                    "limit": feed.get("limit", 10),
                }
            )
        elif isinstance(feed, str):
            result.append(
                {
                    "url": feed,
                    "name": "Unknown Atom",
                    "limit": 10,
                }
            )
    return result


class AtomScraper(BaseScraper):
    """Scraper for Atom feeds."""

    def __init__(self, config_path: str | Path | None = None):
        super().__init__("Atom")
        self.feeds = load_atom_feeds(config_path)
        self.error_logger = create_error_logger("atom_scraper", "logs/errors")

    def scrape(self) -> list[dict[str, Any]]:
        """Scrape all configured Atom feeds with comprehensive error logging."""
        items = []

        if not self.feeds:
            logger.warning("No Atom feeds configured. Skipping scrape.")
            return items

        for feed_info in self.feeds:
            feed_url = feed_info.get("url")
            source_name = feed_info.get("name", "Unknown Atom")
            limit = feed_info.get("limit", 10)

            if not feed_url:
                logger.warning("Skipping empty feed URL.")
                continue

            logger.info(
                f"Scraping Atom feed: {feed_url} (source: {source_name}, limit: {limit})"
            )
            try:
                parsed_feed = feedparser.parse(feed_url)

                logger.debug(
                    "Parsed feed %s (entries=%s, bozo=%s, feed_title=%s)",
                    feed_url,
                    len(getattr(parsed_feed, "entries", []) or []),
                    getattr(parsed_feed, "bozo", False),
                    parsed_feed.feed.get("title") if parsed_feed.feed else "<no-title>",
                )

                # Check for parsing issues
                if parsed_feed.bozo:
                    bozo_exc = parsed_feed.bozo_exception

                    if ENCODING_OVERRIDE_EXCEPTIONS and isinstance(
                        bozo_exc, ENCODING_OVERRIDE_EXCEPTIONS
                    ):
                        logger.debug(
                            "Feed %s has encoding declaration mismatch (CharacterEncodingOverride): %s",
                            feed_url,
                            bozo_exc,
                        )
                    else:
                        # Log detailed parsing error
                        self.error_logger.log_feed_error(
                            feed_url=feed_url,
                            error=bozo_exc,
                            feed_name=parsed_feed.feed.get("title", "Unknown Feed"),
                            operation="feed_parsing",
                        )
                        logger.warning(
                            "Feed %s may be ill-formed: %s", feed_url, bozo_exc
                        )

                # Extract feed name and description
                feed_name = parsed_feed.feed.get("title", "Unknown Feed")
                feed_description = parsed_feed.feed.get("subtitle", "") or parsed_feed.feed.get("description", "")

                logger.info(f"Processing feed: {feed_name} - {feed_description}")

                # Apply limit to entries
                entries_to_process = parsed_feed.entries[:limit]

                processed_entries = 0
                for entry in entries_to_process:
                    item = self._process_entry(
                        entry, feed_name, feed_description, feed_url, source_name
                    )
                    if item:
                        items.append(item)
                        processed_entries += 1

                logger.info(
                    f"Successfully processed {processed_entries} entries from {feed_name} "
                    f"(limit: {limit})"
                )

            except Exception as e:
                # Log comprehensive error details
                self.error_logger.log_feed_error(
                    feed_url=feed_url, error=e, feed_name="Unknown Feed", operation="feed_scraping"
                )
                logger.error(f"Error scraping feed {feed_url}: {e}", exc_info=True)

        logger.info(f"Atom scraping completed. Processed {len(items)} total items")
        return items

    def _process_entry(
        self,
        entry,
        feed_name: str,
        feed_description: str = "",
        feed_url: str = "",
        source_name: str = "",
    ) -> dict[str, Any] | None:
        """Process a single entry from an Atom feed."""
        title = entry.get("title", "No Title")
        link = entry.get("link")

        if not link:
            # Log detailed entry error
            self.error_logger.log_error(
                error=Exception(f"Missing link for entry: {title}"),
                operation="entry_processing",
                context={
                    "feed_url": feed_url,
                    "feed_name": feed_name,
                    "entry_title": title,
                    "entry_id": entry.get("id"),
                    "error_type": "missing_link",
                },
            )
            logger.warning(f"Skipping entry with no link in feed {feed_name}: {title}")
            return None

        # Extract content from Atom entry
        content = ""
        if "content" in entry and entry["content"]:
            for c in entry["content"]:
                if c.get("type") in ("text/html", "html"):
                    content = c.get("value", "")
                    break
        if not content:
            content = entry.get("summary", "")

        logger.debug(
            "Entry debug: feed=%s title=%s content_chars=%s summary_chars=%s link=%s",
            feed_name,
            title,
            len(content or ""),
            len(entry.get("summary", "") or ""),
            link,
        )

        # Parse publication date (Atom uses 'updated' or 'published')
        publication_date = None
        date_field = entry.get("published_parsed") or entry.get("updated_parsed")
        if date_field:
            with contextlib.suppress(TypeError, ValueError):
                publication_date = datetime(*date_field[:6])

        # Determine domain for metadata
        try:
            from urllib.parse import urlparse
            host = urlparse(link).netloc or ""
        except Exception:
            host = ""

        item = {
            "url": self._normalize_url(link),
            "title": title,
            "content_type": ContentType.ARTICLE,
            "metadata": {
                "platform": "atom",  # Scraper identifier
                "source": source_name,  # Configured name from YAML
                "source_domain": host,
                "feed_name": feed_name,
                "feed_description": feed_description,
                "author": entry.get("author"),
                "publication_date": publication_date.isoformat() if publication_date else None,
                "rss_content": content,  # Store content for processing
                "word_count": len(content.split()) if content else 0,
                "entry_id": entry.get("id"),
                "tags": [tag.get("term") for tag in entry.get("tags", []) if tag.get("term")],
            },
        }

        logger.debug(
            "Emitted Atom item: url=%s word_count=%s publication_date=%s tags=%s",
            item["url"],
            item["metadata"].get("word_count"),
            item["metadata"].get("publication_date"),
            item["metadata"].get("tags"),
        )

        return item


def run_atom_scraper():
    """Initialize and run the Atom scraper."""
    scraper = AtomScraper()
    return scraper.run()


if __name__ == "__main__":
    count = run_atom_scraper()
    print(f"Atom scraper processed {count} items")
```

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest app/tests/test_atom_scraper.py -v`
Expected: All 3 tests PASS

**Step 5: Commit scraper implementation**

```bash
git add app/scraping/atom_unified.py app/tests/test_atom_scraper.py
git commit -m "feat: add Atom feed scraper with config loading and feed parsing"
```

---

## Task 3: Register Atom Scraper in Runner

**Files:**
- Modify: `app/scraping/runner.py:1-30`

**Step 1: Write test for scraper registration**

Add to `app/tests/test_atom_scraper.py`:

```python
def test_atom_scraper_in_runner():
    """Test that AtomScraper is registered in ScraperRunner."""
    from app.scraping.runner import ScraperRunner

    runner = ScraperRunner()
    scraper_names = runner.list_scrapers()

    assert "Atom" in scraper_names
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest app/tests/test_atom_scraper.py::test_atom_scraper_in_runner -v`
Expected: FAIL with AssertionError (Atom not in scraper list)

**Step 3: Register AtomScraper in runner**

Edit `app/scraping/runner.py`:

Add import:
```python
from app.scraping.atom_unified import AtomScraper
```

Add to scrapers list in `__init__` method:
```python
def __init__(self):
    self.scrapers: list[BaseScraper] = [
        HackerNewsUnifiedScraper(),
        RedditUnifiedScraper(),
        SubstackScraper(),
        TechmemeScraper(),
        PodcastUnifiedScraper(),
        TwitterUnifiedScraper(),
        YouTubeUnifiedScraper(),
        AtomScraper(),  # Add this line
    ]
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest app/tests/test_atom_scraper.py::test_atom_scraper_in_runner -v`
Expected: PASS

**Step 5: Run full test suite to ensure no regressions**

Run: `source .venv/bin/activate && pytest app/tests/test_atom_scraper.py -v`
Expected: All tests PASS

**Step 6: Commit runner registration**

```bash
git add app/scraping/runner.py app/tests/test_atom_scraper.py
git commit -m "feat: register Atom scraper in ScraperRunner"
```

---

## Task 4: Integration Testing with Real Feed

**Files:**
- Modify: `app/tests/test_atom_scraper.py`

**Step 1: Write integration test with mock feed**

Add to `app/tests/test_atom_scraper.py`:

```python
from unittest.mock import patch, MagicMock


def test_atom_scraper_scrape_integration(tmp_path: Path):
    """Test complete scraping workflow with mocked feed."""
    # Create config
    config_path = tmp_path / "atom.yml"
    config_content = """
feeds:
  - url: "https://example.com/feed.atom"
    name: "Test Feed"
    limit: 2
"""
    config_path.write_text(config_content)

    # Mock feedparser
    mock_feed = MagicMock()
    mock_feed.bozo = False
    mock_feed.feed = {
        "title": "Test Atom Feed",
        "subtitle": "A test feed"
    }
    mock_feed.entries = [
        {
            "title": "Entry 1",
            "link": "https://example.com/entry1",
            "content": [{"type": "text/html", "value": "<p>Content 1</p>"}],
            "published_parsed": (2025, 10, 12, 10, 0, 0),
            "id": "entry1",
            "tags": [{"term": "test"}],
        },
        {
            "title": "Entry 2",
            "link": "https://example.com/entry2",
            "summary": "Summary 2",
            "updated_parsed": (2025, 10, 12, 11, 0, 0),
            "id": "entry2",
            "tags": [],
        },
    ]

    with patch("app.scraping.atom_unified.feedparser.parse", return_value=mock_feed):
        from app.scraping.atom_unified import AtomScraper

        scraper = AtomScraper(config_path)
        items = scraper.scrape()

    assert len(items) == 2
    assert items[0]["url"] == "https://example.com/entry1"
    assert items[0]["title"] == "Entry 1"
    assert items[0]["content_type"] == ContentType.ARTICLE
    assert items[0]["metadata"]["platform"] == "atom"
    assert items[0]["metadata"]["source"] == "Test Feed"
    assert items[0]["metadata"]["feed_name"] == "Test Atom Feed"
    assert "Content 1" in items[0]["metadata"]["rss_content"]
    assert items[0]["metadata"]["tags"] == ["test"]

    assert items[1]["url"] == "https://example.com/entry2"
    assert items[1]["title"] == "Entry 2"
    assert "Summary 2" in items[1]["metadata"]["rss_content"]
```

**Step 2: Run integration test**

Run: `source .venv/bin/activate && pytest app/tests/test_atom_scraper.py::test_atom_scraper_scrape_integration -v`
Expected: PASS

**Step 3: Run complete test suite**

Run: `source .venv/bin/activate && pytest app/tests/ -v -k atom`
Expected: All Atom tests PASS

**Step 4: Commit integration tests**

```bash
git add app/tests/test_atom_scraper.py
git commit -m "test: add integration tests for Atom scraper"
```

---

## Task 5: Verify Scraper Works in Live Environment

**Files:**
- Test: `app/scraping/atom_unified.py`
- Test: `config/atom.yml`

**Step 1: Add a real Atom feed to config**

Edit `config/atom.yml` to add a real feed (or use existing if already configured):

```yaml
feeds:
  - url: "https://example.com/feed.atom"
    name: "Example Feed"
    limit: 5
```

**Step 2: Run the scraper manually**

Run: `source .venv/bin/activate && python -m app.scraping.atom_unified`
Expected: Output showing "Atom scraper processed N items" with no errors

**Step 3: Verify scraper logs**

Run: `tail -20 logs/errors/atom_scraper_*.jsonl` (if any errors occurred)
Expected: No critical errors, or expected warnings for missing feeds

**Step 4: Test via ScraperRunner**

Run: `source .venv/bin/activate && python -c "from app.scraping.runner import ScraperRunner; runner = ScraperRunner(); stats = runner.run_scraper_with_stats('Atom'); print(f'Scraped: {stats.scraped}, Saved: {stats.saved}, Duplicates: {stats.duplicates}, Errors: {stats.errors}')"`
Expected: Statistics output showing scraper ran successfully

**Step 5: Commit any config updates**

```bash
git add config/atom.yml
git commit -m "config: add real Atom feed for testing"
```

---

## Task 6: Update Documentation

**Files:**
- Modify: `ai-memory/README.md` (if exists)
- Or notify user to update documentation

**Step 1: Document new scraper**

If `ai-memory/README.md` exists, add Atom scraper to the list of scrapers:

```markdown
### Scrapers
- **Atom**: Generic Atom feed scraper (`app/scraping/atom_unified.py`)
  - Config: `config/atom.yml`
  - Platform: `atom`
  - Follows same pattern as Substack scraper
```

**Step 2: Verify all tests pass**

Run: `source .venv/bin/activate && pytest app/tests/test_atom_scraper.py -v`
Expected: All tests PASS

**Step 3: Run code quality checks**

Run: `source .venv/bin/activate && ruff check app/scraping/atom_unified.py app/tests/test_atom_scraper.py`
Expected: No errors

Run: `source .venv/bin/activate && ruff format app/scraping/atom_unified.py app/tests/test_atom_scraper.py`
Expected: Files formatted successfully

**Step 4: Final commit**

```bash
git add -A
git commit -m "docs: update documentation for Atom scraper"
```

---

## Summary

This plan creates a new Atom feed scraper following the established patterns:

1. **Config files**: `config/atom.yml` and `config/atom.example.yml`
2. **Scraper module**: `app/scraping/atom_unified.py` with `AtomScraper` class
3. **Tests**: Comprehensive unit and integration tests in `app/tests/test_atom_scraper.py`
4. **Registration**: Added to `ScraperRunner` in `app/scraping/runner.py`
5. **Verification**: Manual testing and documentation updates

**Key Design Decisions:**
- Uses `feedparser` library (same as Substack scraper)
- Follows `BaseScraper` interface with `scrape()` method
- Handles both Atom-specific fields (`published`, `updated`, `subtitle`) and RSS fallbacks
- Uses error logger for comprehensive error tracking
- Platform identifier: `"atom"`
- Source: Configured name from YAML (never overwritten)

**Testing Strategy:**
- Unit tests for config loading
- Integration tests with mocked feeds
- Manual verification with real feeds
- Code quality checks with ruff

**Follow TDD:**
- Write failing test first
- Run to verify failure
- Write minimal implementation
- Run to verify pass
- Commit

**References:**
- Substack scraper: `app/scraping/substack_unified.py`
- Base scraper: `app/scraping/base.py`
- Scraper runner: `app/scraping/runner.py`
