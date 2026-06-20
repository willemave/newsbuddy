"""Shared helpers for feed-subscription pipeline tests."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

from app.pipeline.task_context import TaskContext


def metadata_dict(value: object | None) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def build_task_context(db_session, queue_gateway: Mock) -> TaskContext:
    @contextmanager
    def _db_context():
        yield db_session

    return TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test-worker",
        queue_gateway=queue_gateway,
        db_factory=_db_context,
    )


def stub_successful_initial_backfill(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.backfill_feed_for_config",
        lambda request: SimpleNamespace(
            config_id=request.config_id,
            base_limit=1,
            target_limit=1 + request.count,
            scraped=1,
            saved=1,
            duplicates=0,
            errors=0,
        ),
    )


def stub_feed_validator(monkeypatch, *, title: str = "Detected Feed") -> None:
    monkeypatch.setattr(
        "app.services.scraper_config_validation.FEED_VALIDATOR.validate_feed_url",
        lambda feed_url: {
            "feed_url": feed_url,
            "feed_format": "rss",
            "title": title,
        },
    )


def stub_detector_feed(
    monkeypatch,
    *,
    feed_url: str,
    feed_type: str,
    title: str = "Detected Feed",
) -> None:
    def _validate(_self, candidate_url: str):
        if candidate_url == feed_url:
            return {
                "feed_url": feed_url,
                "feed_format": "rss",
                "title": title,
            }
        return None

    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.FeedDetector.validate_feed_url",
        _validate,
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.FeedDetector.classify_feed_type",
        lambda _self, **_kwargs: SimpleNamespace(feed_type=feed_type),
    )
    stub_feed_validator(monkeypatch, title=title)
