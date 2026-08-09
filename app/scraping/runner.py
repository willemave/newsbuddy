import re

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.domain.scraper_runs import ScraperStats, scraper_run_status
from app.scraping.aggregators import load_aggregator_scrapers
from app.scraping.atom_unified import AtomScraper
from app.scraping.base import BaseScraper
from app.scraping.discussion_comments import DiscussionCommentsScraper
from app.scraping.podcast_unified import PodcastUnifiedScraper
from app.scraping.reddit_unified import RedditUnifiedScraper
from app.scraping.substack_unified import SubstackScraper

logger = get_logger(__name__)


def _normalize_scraper_name(name: str) -> str:
    """Return a stable key for CLI/display-name scraper matching."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _scraper_lookup_keys(scraper: BaseScraper) -> set[str]:
    """Return accepted normalized names for a scraper instance."""
    keys = {scraper.name}
    for attr_name in ("KEY", "DISPLAY_NAME"):
        value = getattr(scraper, attr_name, None)
        if isinstance(value, str) and value:
            keys.add(value)
    return {_normalize_scraper_name(key) for key in keys}


class ScraperRunner:
    """Manages and runs all scrapers."""

    def __init__(self) -> None:
        # News aggregators (HN, Techmeme, Mediagazer, Memeorandum, SciURLs,
        # FinURLs, Brutalist Report) are loaded from ``config/aggregators.yml``
        # so adding a new aggregator is a YAML edit + new subclass under
        # ``app.scraping.aggregators``.
        aggregator_scrapers = load_aggregator_scrapers()
        self.scrapers: list[BaseScraper] = [
            *aggregator_scrapers,
            RedditUnifiedScraper(),
            SubstackScraper(),
            PodcastUnifiedScraper(),
            AtomScraper(),
            DiscussionCommentsScraper(),
        ]

    def run_all_with_stats(self) -> dict[str, ScraperStats]:
        """Run all scrapers and return detailed statistics."""
        logger.info(
            "Starting all scrapers",
            extra=build_log_extra(
                component="scraper_runner",
                operation="run_all",
                event_name="scraper.run",
                status="started",
                context_data={"scraper_count": len(self.scrapers)},
            ),
        )

        results = {}

        # Run scrapers sequentially
        for scraper in self.scrapers:
            try:
                stats = scraper.run_with_stats()
                results[scraper.name] = stats

                logger.info(
                    "Scraper completed",
                    extra=build_log_extra(
                        component="scraper_runner",
                        operation="run_scraper",
                        event_name="scraper.run",
                        status=scraper_run_status(stats),
                        source=scraper.name,
                        context_data={
                            "scraped": stats.scraped,
                            "saved": stats.saved,
                            "duplicates": stats.duplicates,
                            "errors": stats.errors,
                            "error_details": stats.error_details,
                        },
                    ),
                )

            except Exception as e:
                logger.exception(
                    "Scraper failed",
                    extra=build_log_extra(
                        component="scraper_runner",
                        operation="run_scraper",
                        event_name="scraper.run",
                        status="failed",
                        source=scraper.name,
                        context_data={"failure_class": type(e).__name__},
                    ),
                )
                results[scraper.name] = ScraperStats(errors=1, error_details=[str(e)])

        total_saved = sum(stat.saved for stat in results.values())
        statuses = [scraper_run_status(stat) for stat in results.values()]
        failed_count = statuses.count("failed")
        degraded_count = statuses.count("degraded")
        logger.info(
            "All scrapers complete",
            extra=build_log_extra(
                component="scraper_runner",
                operation="run_all",
                event_name="scraper.run",
                status=(
                    "failed" if failed_count else "degraded" if degraded_count else "completed"
                ),
                context_data={
                    "total_saved": total_saved,
                    "scraper_count": len(results),
                    "failed_count": failed_count,
                    "degraded_count": degraded_count,
                },
            ),
        )

        return results

    def run_scraper_with_stats(self, name: str) -> ScraperStats | None:
        """Run a specific scraper by name and return detailed statistics."""
        requested_name = _normalize_scraper_name(name)
        for scraper in self.scrapers:
            if requested_name in _scraper_lookup_keys(scraper):
                try:
                    stats = scraper.run_with_stats()

                    logger.info(
                        "Scraper completed",
                        extra=build_log_extra(
                            component="scraper_runner",
                            operation="run_scraper",
                            event_name="scraper.run",
                            status=scraper_run_status(stats),
                            source=name,
                            context_data={
                                "scraped": stats.scraped,
                                "saved": stats.saved,
                                "duplicates": stats.duplicates,
                                "errors": stats.errors,
                                "error_details": stats.error_details,
                            },
                        ),
                    )

                    return stats
                except Exception as e:
                    logger.exception(
                        "Scraper failed",
                        extra=build_log_extra(
                            component="scraper_runner",
                            operation="run_scraper",
                            event_name="scraper.run",
                            status="failed",
                            source=name,
                            context_data={"failure_class": type(e).__name__},
                        ),
                    )

                    return ScraperStats(errors=1, error_details=[str(e)])

        logger.error(
            "Scraper not found",
            extra=build_log_extra(
                component="scraper_runner",
                operation="run_scraper",
                event_name="scraper.run",
                status="failed",
                source=name,
                context_data={"failure_class": "ScraperNotFound"},
            ),
        )
        return None

    def list_scrapers(self) -> list[str]:
        """List all available scrapers."""
        return [scraper.name for scraper in self.scrapers]
