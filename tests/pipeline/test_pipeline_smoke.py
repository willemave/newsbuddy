"""Smoke test for scrape -> process -> summarize pipeline flow."""

from contextlib import contextmanager
from unittest.mock import Mock, patch

from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content
from app.models.metadata.summaries import BulletedSummary, BulletSummaryPoint, ContentQuote
from app.pipeline.sequential_task_processor import SequentialTaskProcessor
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.scraping.base import BaseScraper
from app.services.queue import QueueService, TaskType


@contextmanager
def _override_get_db(db_session):
    try:
        yield db_session
        db_session.commit()
    except Exception:
        db_session.rollback()
        raise


class DummyScraper(BaseScraper):
    """Scraper stub that returns a single article item."""

    def __init__(self) -> None:
        super().__init__("Dummy")

    def scrape(self) -> list[dict]:
        return [
            {
                "url": "https://example.com/smoke",
                "title": "Smoke Article",
                "content_type": ContentType.ARTICLE,
                "metadata": {"platform": "dummy", "source": "example.com"},
            }
        ]


def _patch_db_access(monkeypatch, db_session):
    def override():
        return _override_get_db(db_session)

    import app.core.db as core_db
    import app.pipeline.task_context as task_context
    import app.pipeline.worker as pipeline_worker
    import app.scraping.base as scraping_base
    import app.services.queue as queue_service

    monkeypatch.setattr(core_db, "get_db", override)
    monkeypatch.setattr(task_context, "get_db", override)
    monkeypatch.setattr(pipeline_worker, "get_db", override)
    monkeypatch.setattr(scraping_base, "get_db", override)
    monkeypatch.setattr(queue_service, "get_db", override)

    return override


def test_scrape_to_completion_smoke(db_session, monkeypatch) -> None:
    db_override = _patch_db_access(monkeypatch, db_session)

    scraper = DummyScraper()
    stats = scraper.run_with_stats()
    assert stats.saved == 1

    queue_service = QueueService()

    with (
        patch("app.pipeline.worker.get_http_service") as mock_http_service,
        patch("app.pipeline.sequential_task_processor.get_llm_service") as mock_llm_service,
        patch("app.pipeline.worker.get_strategy_registry") as mock_registry,
    ):
        mock_http_service.return_value = Mock()

        mock_llm = Mock()
        mock_llm.summarize.return_value = BulletedSummary(
            title="Article Title",
            points=[
                BulletSummaryPoint(
                    text=f"Point {idx + 1} highlights a key takeaway.",
                    detail=(
                        "This detail expands on the takeaway with concrete evidence "
                        "and explains why it matters for the reader."
                    ),
                    quotes=[
                        ContentQuote(
                            text="This supporting quote provides additional context.",
                            context="Test Source",
                            attribution=None,
                        )
                    ],
                )
                for idx in range(10)
            ],
            classification="to_read",
        )
        mock_llm_service.return_value = mock_llm

        mock_strategy = Mock()
        mock_strategy.preprocess_url.return_value = "https://example.com/smoke"
        mock_strategy.download_content.return_value = "<html>Smoke content</html>"
        mock_strategy.extract_data.return_value = {
            "title": "Smoke Article",
            "text_content": "Smoke content",
            "author": None,
            "publication_date": None,
            "content_type": "html",
            "final_url_after_redirects": "https://example.com/smoke",
        }
        mock_strategy.prepare_for_llm.return_value = {"content_to_summarize": "Smoke content"}
        mock_strategy.extract_internal_urls.return_value = []

        mock_registry_instance = Mock()
        mock_registry_instance.get_strategy.return_value = mock_strategy
        mock_registry.return_value = mock_registry_instance

        processor = SequentialTaskProcessor()
        processor.context = TaskContext(
            queue_service=processor.queue_service,
            settings=processor.settings,
            llm_service=processor.llm_service,
            worker_id=processor.worker_id,
            db_factory=db_override,
        )

        task = queue_service.dequeue(worker_id="test-worker")
        assert task is not None
        result = processor.process_task(TaskEnvelope.from_queue_data(task))
        queue_service.complete_task(
            task["id"], success=result.success, error_message=result.error_message
        )

        summarize_task = queue_service.dequeue(
            task_type=TaskType.SUMMARIZE, worker_id="test-worker"
        )
        assert summarize_task is not None
        result = processor.process_task(TaskEnvelope.from_queue_data(summarize_task))
        queue_service.complete_task(
            summarize_task["id"], success=result.success, error_message=result.error_message
        )

    content = db_session.query(Content).filter(Content.url == "https://example.com/smoke").first()
    assert content is not None
    assert content.status == ContentStatus.AWAITING_IMAGE.value
