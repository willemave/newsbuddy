"""Tests for scrape task onboarding progress."""

from contextlib import nullcontext
from unittest.mock import Mock

from app.models.domain.scraper_runs import ScraperStats
from app.pipeline.handlers.scrape import ScrapeHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType


def test_scrape_handler_records_processed_items_by_source(monkeypatch) -> None:
    recorded_progress: dict[str, object] = {}
    runner = Mock()
    runner.run_scraper_with_stats.side_effect = [
        ScraperStats(saved=18, duplicates=10),
        ScraperStats(
            saved=14,
            duplicates=5,
            processed_by_config_id={41: 11, 42: 8},
        ),
    ]
    monkeypatch.setattr("app.pipeline.handlers.scrape.ScraperRunner", lambda: runner)

    def fake_mark_complete(_db, **kwargs):
        recorded_progress.update(kwargs)
        return len(kwargs["scraper_keys"])

    monkeypatch.setattr(
        "app.pipeline.handlers.scrape.mark_scraper_sources_complete",
        fake_mark_complete,
    )

    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
        db_factory=lambda: nullcontext(Mock()),
    )
    task = TaskEnvelope(
        id=1,
        task_type=TaskType.SCRAPE,
        retry_count=0,
        payload={"sources": ["techmeme", "reddit"]},
    )

    result = ScrapeHandler().handle(task, context)

    assert result.success is True
    assert recorded_progress == {
        "scraper_keys": ["techmeme", "reddit"],
        "processed_item_counts": {"techmeme": 28, "reddit": 19},
        "processed_item_counts_by_config_id": {41: 11, 42: 8},
    }
