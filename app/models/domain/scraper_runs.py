from dataclasses import dataclass, field
from typing import Literal, Protocol

type ScraperRunStatus = Literal["completed", "degraded", "failed"]


class ScraperRunMetrics(Protocol):
    """Minimum result shape needed to classify a scraper run."""

    saved: int
    duplicates: int
    errors: int


@dataclass
class ScraperStats:
    """Statistics for a scraper run."""

    scraped: int = 0
    saved: int = 0
    duplicates: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)
    processed_by_config_id: dict[int, int] = field(default_factory=dict)


def is_zero_progress_scrape_failure(stats: ScraperRunMetrics | None) -> bool:
    """Return whether a scraper failed without saving or reusing any items."""
    return scraper_run_status(stats) == "failed"


def scraper_run_status(stats: ScraperRunMetrics | None) -> ScraperRunStatus:
    """Classify one scraper result consistently across runners and handlers."""
    if stats is None or (stats.errors > 0 and stats.saved + stats.duplicates <= 0):
        return "failed"
    if stats.errors > 0:
        return "degraded"
    return "completed"
