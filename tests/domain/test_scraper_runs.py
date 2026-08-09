import pytest

from app.models.domain.scraper_runs import (
    ScraperRunMetrics,
    ScraperStats,
    scraper_run_status,
)
from app.models.internal.feed_backfill import FeedBackfillResult


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        (None, "failed"),
        (ScraperStats(), "completed"),
        (ScraperStats(errors=1), "failed"),
        (ScraperStats(saved=1, errors=1), "degraded"),
        (ScraperStats(duplicates=1, errors=1), "degraded"),
        (
            FeedBackfillResult(
                config_id=1,
                base_limit=10,
                target_limit=10,
                scraped=1,
                saved=1,
                duplicates=0,
                errors=1,
            ),
            "degraded",
        ),
    ],
)
def test_scraper_run_status_classifies_progress_and_errors(
    stats: ScraperRunMetrics | None,
    expected: str,
) -> None:
    assert scraper_run_status(stats) == expected
