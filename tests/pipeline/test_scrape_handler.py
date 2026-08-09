"""Tests for scrape task onboarding progress."""

from contextlib import nullcontext
from unittest.mock import Mock

from app.models.contracts import BriefingFirstRunSourceOutcome
from app.models.domain.scraper_runs import ScraperStats
from app.pipeline.handlers.scrape import ScrapeHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType


def test_scrape_handler_records_processed_items_by_source(monkeypatch) -> None:
    recorded_progress: list[dict[str, object]] = []
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

    def fake_record_result(_db, **kwargs):
        recorded_progress.append(kwargs)
        return 1

    monkeypatch.setattr(
        "app.pipeline.handlers.scrape.record_scraper_source_result",
        fake_record_result,
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
        payload={"sources": ["techmeme", "reddit"], "first_edition_run_id": 99},
    )

    result = ScrapeHandler().handle(task, context)

    assert result.success is True
    assert recorded_progress == [
        {
            "run_id": 99,
            "scraper_key": "techmeme",
            "processed_item_count": 28,
            "processed_item_counts_by_config_id": {},
            "outcome": BriefingFirstRunSourceOutcome.PROCESSED,
        },
        {
            "run_id": 99,
            "scraper_key": "reddit",
            "processed_item_count": 19,
            "processed_item_counts_by_config_id": {41: 11, 42: 8},
            "outcome": BriefingFirstRunSourceOutcome.PROCESSED,
        },
    ]


def test_scrape_handler_records_failure_and_continues_remaining_sources(monkeypatch) -> None:
    recorded_progress: list[dict[str, object]] = []
    runner = Mock()
    runner.run_scraper_with_stats.side_effect = [
        RuntimeError("source unavailable"),
        ScraperStats(saved=4, duplicates=2),
    ]
    monkeypatch.setattr("app.pipeline.handlers.scrape.ScraperRunner", lambda: runner)

    def fake_record_result(_db, **kwargs):
        recorded_progress.append(kwargs)
        return 1

    monkeypatch.setattr(
        "app.pipeline.handlers.scrape.record_scraper_source_result",
        fake_record_result,
    )
    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
        db_factory=lambda: nullcontext(Mock()),
    )
    task = TaskEnvelope(
        id=2,
        task_type=TaskType.SCRAPE,
        retry_count=0,
        payload={"sources": ["techmeme", "reddit"], "first_edition_run_id": 99},
    )

    result = ScrapeHandler().handle(task, context)

    assert result.success is False
    assert runner.run_scraper_with_stats.call_count == 2
    assert recorded_progress == [
        {
            "run_id": 99,
            "scraper_key": "techmeme",
            "processed_item_count": 0,
            "processed_item_counts_by_config_id": {},
            "outcome": BriefingFirstRunSourceOutcome.UNAVAILABLE,
        },
        {
            "run_id": 99,
            "scraper_key": "reddit",
            "processed_item_count": 6,
            "processed_item_counts_by_config_id": {},
            "outcome": BriefingFirstRunSourceOutcome.PROCESSED,
        },
    ]


def test_scrape_handler_fails_when_runner_returns_outage_stats(monkeypatch) -> None:
    runner = Mock()
    runner.run_scraper_with_stats.return_value = ScraperStats(
        errors=1,
        error_details=["sciurls: E2B unavailable"],
    )
    monkeypatch.setattr("app.pipeline.handlers.scrape.ScraperRunner", lambda: runner)
    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
        db_factory=lambda: nullcontext(Mock()),
    )
    task = TaskEnvelope(
        id=4,
        task_type=TaskType.SCRAPE,
        retry_count=0,
        payload={"sources": ["sciurls"]},
    )

    result = ScrapeHandler().handle(task, context)

    assert result.success is False
    assert result.retryable is True
    assert result.error_message == "Scraper sources failed: sciurls"


def test_scrape_handler_succeeds_when_source_preserves_partial_progress(monkeypatch) -> None:
    runner = Mock()
    runner.run_scraper_with_stats.return_value = ScraperStats(saved=1, errors=1)
    monkeypatch.setattr("app.pipeline.handlers.scrape.ScraperRunner", lambda: runner)
    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
        db_factory=lambda: nullcontext(Mock()),
    )
    task = TaskEnvelope(
        id=5,
        task_type=TaskType.SCRAPE,
        retry_count=0,
        payload={"sources": ["techmeme"]},
    )

    result = ScrapeHandler().handle(task, context)

    assert result.success is True


def test_scrape_handler_all_succeeds_when_sources_preserve_partial_progress(
    monkeypatch,
) -> None:
    runner = Mock()
    runner.run_all_with_stats.return_value = {
        "techmeme": ScraperStats(duplicates=1, errors=1),
        "hackernews": ScraperStats(saved=2),
    }
    monkeypatch.setattr("app.pipeline.handlers.scrape.ScraperRunner", lambda: runner)
    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
        db_factory=lambda: nullcontext(Mock()),
    )
    task = TaskEnvelope(
        id=6,
        task_type=TaskType.SCRAPE,
        retry_count=0,
        payload={"sources": ["all"]},
    )

    result = ScrapeHandler().handle(task, context)

    assert result.success is True


def test_scrape_handler_retries_when_progress_cannot_be_recorded(monkeypatch) -> None:
    runner = Mock()
    runner.run_scraper_with_stats.return_value = ScraperStats(saved=4)
    monkeypatch.setattr("app.pipeline.handlers.scrape.ScraperRunner", lambda: runner)
    monkeypatch.setattr(
        "app.pipeline.handlers.scrape.record_scraper_source_result",
        Mock(side_effect=RuntimeError("database unavailable")),
    )
    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
        db_factory=lambda: nullcontext(Mock()),
    )
    task = TaskEnvelope(
        id=3,
        task_type=TaskType.SCRAPE,
        retry_count=0,
        payload={"sources": ["techmeme"], "first_edition_run_id": 99},
    )

    result = ScrapeHandler().handle(task, context)

    assert result.success is False
