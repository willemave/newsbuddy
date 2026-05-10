from dataclasses import dataclass, field


@dataclass
class ScraperStats:
    """Statistics for a scraper run."""

    scraped: int = 0
    saved: int = 0
    duplicates: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)
