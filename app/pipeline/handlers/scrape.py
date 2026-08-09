"""Scrape task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.contracts import BriefingFirstRunSourceOutcome
from app.models.domain.scraper_runs import ScraperStats, is_zero_progress_scrape_failure
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.pipeline.task_specs import ScrapePayload
from app.scraping.runner import ScraperRunner
from app.services.briefing.first_run import record_scraper_source_result
from app.services.queue import TaskType

logger = get_logger(__name__)


class ScrapeHandler:
    """Handle scrape tasks."""

    task_type = TaskType.SCRAPE

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Run configured scrapers."""
        try:
            request = ScrapePayload.model_validate(task.payload or {})
            sources = request.sources
            runner = ScraperRunner()

            if sources == ["all"]:
                results = runner.run_all_with_stats()
                all_source_failures = [
                    name
                    for name, stats in results.items()
                    if is_zero_progress_scrape_failure(stats)
                ]
                if all_source_failures:
                    return TaskResult.fail(
                        f"Scraper sources failed: {', '.join(all_source_failures)}",
                        retryable=True,
                    )
                return TaskResult.ok()

            failures: list[str] = []
            progress_recording_failed = False
            for source in sources:
                try:
                    stats = runner.run_scraper_with_stats(source)
                    progress_recording_failed |= not _record_progress(
                        context,
                        run_id=request.first_edition_run_id,
                        source=source,
                        stats=stats,
                    )
                    if is_zero_progress_scrape_failure(stats):
                        failures.append(source)
                except Exception as exc:  # noqa: BLE001
                    failures.append(source)
                    progress_recording_failed |= not _record_progress(
                        context,
                        run_id=request.first_edition_run_id,
                        source=source,
                        stats=None,
                    )
                    logger.exception(
                        "Scraper source failed",
                        extra={
                            "component": "scrape",
                            "operation": "run_source",
                            "context_data": {
                                "source": source,
                                "run_id": request.first_edition_run_id,
                                "error": str(exc),
                            },
                        },
                    )
            if progress_recording_failed:
                return TaskResult.fail("Could not record onboarding scraper progress")
            if failures:
                return TaskResult.fail(
                    f"Scraper sources failed: {', '.join(failures)}",
                    retryable=True,
                )
            return TaskResult.ok()
        except Exception as exc:  # noqa: BLE001
            logger.error("Scraper error: %s", exc, exc_info=True)
            return TaskResult.fail(str(exc))


def _processed_item_count(stats: ScraperStats | None) -> int:
    if stats is None:
        return 0
    return max(stats.saved + stats.duplicates, 0)


def _processed_config_counts(stats: ScraperStats | None) -> dict[int, int]:
    if stats is None:
        return {}
    return {
        config_id: max(item_count, 0)
        for config_id, item_count in stats.processed_by_config_id.items()
    }


def _record_progress(
    context: TaskContext,
    *,
    run_id: int | None,
    source: str,
    stats: ScraperStats | None,
) -> bool:
    if run_id is None:
        return True
    processed_item_count = _processed_item_count(stats)
    outcome = (
        BriefingFirstRunSourceOutcome.UNAVAILABLE
        if is_zero_progress_scrape_failure(stats)
        else BriefingFirstRunSourceOutcome.PROCESSED
    )
    try:
        with context.db_factory() as db:
            record_scraper_source_result(
                db,
                run_id=run_id,
                scraper_key=source,
                processed_item_count=processed_item_count,
                processed_item_counts_by_config_id=_processed_config_counts(stats),
                outcome=outcome,
            )
        return True
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not record onboarding scraper progress",
            extra={
                "component": "scrape",
                "operation": "record_onboarding_progress",
                "context_data": {"run_id": run_id, "source": source},
            },
        )
        return False
