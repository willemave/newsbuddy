from app.core.logging import get_logger
from app.models.scraper_runs import ScraperStats
from app.scraping.atom_unified import AtomScraper
from app.scraping.base import BaseScraper
from app.scraping.hackernews_unified import HackerNewsUnifiedScraper
from app.scraping.podcast_unified import PodcastUnifiedScraper
from app.scraping.reddit_unified import RedditUnifiedScraper
from app.scraping.substack_unified import SubstackScraper
from app.scraping.techmeme_unified import TechmemeScraper
from app.scraping.twitter_unified import TwitterUnifiedScraper

# from app.scraping.youtube_unified import YouTubeUnifiedScraper
from app.services.event_logger import log_event

logger = get_logger(__name__)


class ScraperRunner:
    """Manages and runs all scrapers."""

    def __init__(self):
        self.scrapers: list[BaseScraper] = [
            HackerNewsUnifiedScraper(),
            RedditUnifiedScraper(),
            SubstackScraper(),
            TechmemeScraper(),
            PodcastUnifiedScraper(),
            TwitterUnifiedScraper(),
            # YouTubeUnifiedScraper(),  # Disabled - not working
            AtomScraper(),
        ]

    def run_all(self) -> dict[str, int]:
        """Run all scrapers and return results. Returns counts for backward compatibility."""
        stats = self.run_all_with_stats()
        return {name: stat.saved for name, stat in stats.items()}

    def run_all_with_stats(self) -> dict[str, ScraperStats]:
        """Run all scrapers and return detailed statistics."""
        logger.info("Starting all scrapers")

        results = {}

        # Run scrapers sequentially
        for scraper in self.scrapers:
            try:
                stats = scraper.run_with_stats()
                results[scraper.name] = stats

                # Log scraper stats to event log
                log_event(
                    event_type="scraper_stats",
                    event_name=scraper.name,
                    scraped=stats.scraped,
                    saved=stats.saved,
                    duplicates=stats.duplicates,
                    errors=stats.errors,
                    error_details=stats.error_details,
                )

            except Exception as e:
                logger.error(f"Scraper {scraper.name} failed: {e}")
                results[scraper.name] = ScraperStats(errors=1, error_details=[str(e)])

                # Log scraper error to event log
                log_event(
                    event_type="scraper_error",
                    event_name=scraper.name,
                    error=str(e),
                    error_type=type(e).__name__,
                )

        total_saved = sum(stat.saved for stat in results.values())
        logger.info(f"All scrapers complete. Total items saved: {total_saved}")

        return results

    def run_scraper(self, name: str) -> int | None:
        """Run a specific scraper by name. Returns count for backward compatibility."""
        stats = self.run_scraper_with_stats(name)
        return stats.saved if stats else None

    def run_scraper_with_stats(self, name: str) -> ScraperStats | None:
        """Run a specific scraper by name and return detailed statistics."""
        for scraper in self.scrapers:
            if scraper.name.lower() == name.lower():
                try:
                    stats = scraper.run_with_stats()

                    # Log scraper stats to event log
                    log_event(
                        event_type="scraper_stats",
                        event_name=name,
                        scraped=stats.scraped,
                        saved=stats.saved,
                        duplicates=stats.duplicates,
                        errors=stats.errors,
                        error_details=stats.error_details,
                    )

                    return stats
                except Exception as e:
                    logger.error(f"Scraper {name} failed: {e}")

                    # Log scraper error to event log
                    log_event(
                        event_type="scraper_error",
                        event_name=name,
                        error=str(e),
                        error_type=type(e).__name__,
                    )

                    return ScraperStats(errors=1, error_details=[str(e)])

        logger.error(f"Scraper not found: {name}")
        return None

    def list_scrapers(self) -> list[str]:
        """List all available scrapers."""
        return [scraper.name for scraper in self.scrapers]
