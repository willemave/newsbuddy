from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import Mock

from app.models.contracts import LearningDeckRunStatus, LearningDeckSourceKind, TaskType
from app.models.db import LearningDeck, LearningDeckRun
from app.pipeline.handlers.generate_learning_deck import GenerateLearningDeckHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.learning_deck_generation import LearningDeckGenerationWaiting


@contextmanager
def _db_factory(db_session):
    yield db_session


def _context(db_session) -> TaskContext:
    return TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test-worker",
        db_factory=lambda: _db_factory(db_session),
    )


def test_generate_learning_deck_handler_calls_generation(db_session, monkeypatch) -> None:
    called: list[int] = []

    def fake_generate(db, *, learning_deck_run_id: int):
        assert db is db_session
        called.append(learning_deck_run_id)

    monkeypatch.setattr(
        "app.pipeline.handlers.generate_learning_deck.generate_learning_deck",
        fake_generate,
    )

    result = GenerateLearningDeckHandler().handle(
        TaskEnvelope(
            id=1,
            task_type=TaskType.GENERATE_LEARNING_DECK,
            payload={"learning_deck_run_id": 42, "user_id": 1},
        ),
        _context(db_session),
    )

    assert result.success is True
    assert called == [42]


def test_generate_learning_deck_handler_defers_without_retry_when_source_waits(
    db_session,
    monkeypatch,
) -> None:
    def fake_generate(_db, *, learning_deck_run_id: int):
        assert learning_deck_run_id == 42
        raise LearningDeckGenerationWaiting("Source still processing", retry_delay_seconds=123)

    monkeypatch.setattr(
        "app.pipeline.handlers.generate_learning_deck.generate_learning_deck",
        fake_generate,
    )

    result = GenerateLearningDeckHandler().handle(
        TaskEnvelope(
            id=1,
            task_type=TaskType.GENERATE_LEARNING_DECK,
            payload={"learning_deck_run_id": 42, "user_id": 1},
        ),
        _context(db_session),
    )

    assert result.success is False
    assert result.retryable is False
    assert result.deferred is True
    assert result.retry_delay_seconds == 123


def test_generate_learning_deck_handler_terminalizes_unexpected_failure(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    deck = LearningDeck(
        user_id=test_user.id,
        source_kind=LearningDeckSourceKind.CONTENT.value,
        source_identity="content:legacy-handler-failure",
        source_metadata={},
        title="Legacy handler failure",
        artifact_object_keys=[],
    )
    db_session.add(deck)
    db_session.flush()
    run = LearningDeckRun(
        deck_id=deck.id,
        user_id=test_user.id,
        status=LearningDeckRunStatus.GENERATING.value,
        source_snapshot={},
        timeline=[],
        artifact_object_keys=[],
    )
    db_session.add(run)
    db_session.commit()

    def fail_generation(*_args, **_kwargs) -> None:
        raise RuntimeError("worker exploded")

    monkeypatch.setattr(
        "app.pipeline.handlers.generate_learning_deck.generate_learning_deck",
        fail_generation,
    )

    result = GenerateLearningDeckHandler().handle(
        TaskEnvelope(
            id=2,
            task_type=TaskType.GENERATE_LEARNING_DECK,
            payload={"learning_deck_run_id": run.id, "user_id": test_user.id},
        ),
        _context(db_session),
    )

    db_session.refresh(run)
    assert result.success is False
    assert result.retryable is False
    assert run.status == LearningDeckRunStatus.FAILED.value
    assert run.error_message == "worker exploded"
