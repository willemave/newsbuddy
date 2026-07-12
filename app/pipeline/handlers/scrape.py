"""Scrape task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.domain.scraper_runs import ScraperStats
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.scraping.runner import ScraperRunner
from app.services.briefing.first_run import mark_scraper_sources_complete
from app.services.queue import TaskType

logger = get_logger(__name__)


class ScrapeHandler:
    """Handle scrape tasks."""

    task_type = TaskType.SCRAPE

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Run configured scrapers."""
        try:
            payload = task.payload or {}
            sources = payload.get("sources", ["all"])
            runner = ScraperRunner()
            processed_item_counts: dict[str, int] = {}
            processed_item_counts_by_config_id: dict[int, int] = {}

            if sources == ["all"]:
                stats_by_source = runner.run_all_with_stats()
                completed_sources = list(stats_by_source)
                for source, aggregate_stats in stats_by_source.items():
                    processed_item_counts[source] = _processed_item_count(aggregate_stats)
                    processed_item_counts_by_config_id.update(
                        _processed_config_counts(aggregate_stats)
                    )
            else:
                completed_sources = [str(source) for source in sources]
                for source in sources:
                    selected_stats = runner.run_scraper_with_stats(source)
                    if selected_stats is None:
                        processed_item_counts[str(source)] = 0
                        continue
                    if not isinstance(selected_stats, ScraperStats):
                        legacy_saved_count = runner.run_scraper(source)
                        processed_item_counts[str(source)] = (
                            max(legacy_saved_count, 0) if isinstance(legacy_saved_count, int) else 0
                        )
                        continue
                    processed_item_counts[str(source)] = _processed_item_count(selected_stats)
                    processed_item_counts_by_config_id.update(
                        _processed_config_counts(selected_stats)
                    )
            try:
                with context.db_factory() as db:
                    mark_scraper_sources_complete(
                        db,
                        scraper_keys=completed_sources,
                        processed_item_counts=processed_item_counts,
                        processed_item_counts_by_config_id=(processed_item_counts_by_config_id),
                    )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Could not record onboarding scraper progress",
                    extra={
                        "component": "scrape",
                        "operation": "record_onboarding_progress",
                    },
                )
            return TaskResult.ok()
        except Exception as exc:  # noqa: BLE001
            logger.error("Scraper error: %s", exc, exc_info=True)
            return TaskResult.fail(str(exc))


def _processed_item_count(stats: object) -> int:
    saved = getattr(stats, "saved", 0)
    duplicates = getattr(stats, "duplicates", 0)
    if not isinstance(saved, int) or not isinstance(duplicates, int):
        return 0
    return max(saved + duplicates, 0)


def _processed_config_counts(stats: object) -> dict[int, int]:
    raw_counts = getattr(stats, "processed_by_config_id", {})
    if not isinstance(raw_counts, dict):
        return {}
    return {
        config_id: max(item_count, 0)
        for config_id, item_count in raw_counts.items()
        if isinstance(config_id, int) and isinstance(item_count, int)
    }
