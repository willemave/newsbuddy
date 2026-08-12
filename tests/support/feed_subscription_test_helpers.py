"""Shared helpers for feed-subscription pipeline tests."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

from app.pipeline.task_context import TaskContext
from app.services import feed_subscription_resolution
from app.services.feed_detection import FeedDetector


def metadata_dict(value: object | None) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def build_task_context(db_session, queue_gateway: Mock) -> TaskContext:
    @contextmanager
    def _db_context():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    return TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test-worker",
        queue_gateway=queue_gateway,
        db_factory=_db_context,
    )


def stub_feed_subscription_runtime(monkeypatch) -> None:
    """Route feed-subscription probes through a deterministic fake sandbox."""

    class _SandboxHttpService:
        def fetch(self, url: str, **_kwargs):
            body, headers = feed_subscription_resolution.get_http_gateway().fetch_content(url)
            content = body.encode() if isinstance(body, str) else body
            return SimpleNamespace(
                url=url,
                status_code=200,
                headers=headers,
                content=content,
                text=content.decode("utf-8", errors="ignore"),
            )

    @contextmanager
    def _runtime(**_kwargs):
        http_service = _SandboxHttpService()
        detector = FeedDetector(
            http_service=http_service,
        )
        yield SimpleNamespace(detector=detector, http_service=http_service)

    monkeypatch.setattr(feed_subscription_resolution, "feed_research_runtime", _runtime)


def stub_feed_validator(monkeypatch, *, title: str = "Detected Feed") -> None:
    @contextmanager
    def _runtime(**_kwargs):
        detector = SimpleNamespace(
            validate_feed_url=lambda feed_url: {
                "feed_url": feed_url,
                "feed_format": "rss",
                "title": title,
            }
        )
        yield SimpleNamespace(detector=detector)

    monkeypatch.setattr(
        "app.services.scraper_config_validation.feed_research_runtime",
        _runtime,
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
