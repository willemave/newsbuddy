"""Exact-lease orchestration for queued chat provider work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.db import ChatSession
from app.models.internal.chat_turn import ChatTurnSessionSnapshot
from app.services.chat_partial_stream import (
    DurableChatPartialWriter,
    initialize_chat_stream_attempt,
)
from app.services.chat_turn_runtime import (
    ChatTurnLeaseCheckError,
    ChatTurnOwnershipLost,
    ChatUsageSnapshot,
    DetachedChatTurn,
    DetachedChatTurnLifecycle,
    QueuedChatTurnOutcome,
    complete_detached_chat_turn,
    mark_detached_chat_turn_running,
    require_current_chat_lease,
    snapshot_detached_chat_turn_from_snapshot,
    start_detached_chat_turn,
)
from app.services.llm_task_turn_tracker import LlmTaskTurnTracker

logger = get_logger(__name__)


@dataclass(frozen=True)
class QueuedChatTurnResult[PreparedT, ExecutedT]:
    """Detached outcome and timings for one queued-turn execution."""

    outcome: QueuedChatTurnOutcome
    turn: DetachedChatTurn | None
    prepared: PreparedT | None
    executed: ExecutedT | None
    external_ms: float = 0
    persistence_ms: float = 0
    partial_write_count: int = 0
    first_partial_ms: float | None = None


def _cancel_detached_attempt(
    *,
    session_factory: Callable[[], Session],
    tracker: LlmTaskTurnTracker,
    note: str,
    error_type: str = "LeaseOwnershipLost",
    error_message: str = "Queue lease ownership was lost before terminal persistence",
) -> None:
    """Best-effort cancellation for a superseded LLM attempt ledger row."""
    try:
        with session_factory() as db:
            tracker.cancelled(
                db,
                note=note,
                error_type=error_type,
                error_message=error_message,
            )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to cancel superseded chat turn ledger",
            extra={"llm_task_id": tracker.task_id},
        )


async def execute_queued_chat_turn[PreparedT, ExecutedT](
    *,
    session_factory: Callable[[], Session],
    session_snapshot: ChatTurnSessionSnapshot,
    session_id: int,
    message_id: int,
    source: str,
    task_id: int | None,
    stream_generation: int,
    lifecycle: DetachedChatTurnLifecycle,
    input_json: Callable[[DetachedChatTurn], dict[str, object]],
    prepare: Callable[[Session, DetachedChatTurn], PreparedT],
    execute: Callable[
        [PreparedT, DetachedChatTurn, DurableChatPartialWriter],
        Awaitable[ExecutedT],
    ],
    persist: Callable[[Session, ExecutedT, DetachedChatTurn], dict[str, object]],
    mark_message_failed: Callable[[Session, int, str, int], object],
    raw_result: Callable[[ExecutedT], object],
    record_usage: Callable[[object, ChatUsageSnapshot, int, int | None, str], None],
    ensure_lease: Callable[[], bool],
    cleanup: Callable[[PreparedT], None],
    after_persist: Callable[[Session, ChatSession], None] | None = None,
    public_failure_message: str = "This chat turn could not be completed. Please retry.",
) -> QueuedChatTurnResult[PreparedT, ExecutedT]:
    """Run prepare, provider, and persistence under exact queue ownership."""
    tracker = LlmTaskTurnTracker(task_id=None)
    turn: DetachedChatTurn | None = None
    prepared: PreparedT | None = None
    executed: ExecutedT | None = None
    partial_writer: DurableChatPartialWriter | None = None
    external_ms = 0.0
    persistence_ms = 0.0

    def build_result(
        outcome: QueuedChatTurnOutcome,
    ) -> QueuedChatTurnResult[PreparedT, ExecutedT]:
        return QueuedChatTurnResult(
            outcome=outcome,
            turn=turn,
            prepared=prepared,
            executed=executed,
            external_ms=external_ms,
            persistence_ms=persistence_ms,
            partial_write_count=partial_writer.write_count if partial_writer is not None else 0,
            first_partial_ms=(
                partial_writer.first_partial_ms if partial_writer is not None else None
            ),
        )

    def cancel_result(note: str) -> QueuedChatTurnResult[PreparedT, ExecutedT]:
        _cancel_detached_attempt(
            session_factory=session_factory,
            tracker=tracker,
            note=note,
        )
        return build_result(QueuedChatTurnOutcome.OWNERSHIP_LOST)

    try:
        with session_factory() as prepare_db:
            session = prepare_db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session is None:
                raise ValueError(f"Chat session {session_id} not found")
            if session_snapshot.effective_session_id != session_id:
                raise ValueError("Queued chat context does not match the requested session")

            initialize_chat_stream_attempt(
                prepare_db,
                message_id=message_id,
                stream_generation=stream_generation,
            )
            turn = snapshot_detached_chat_turn_from_snapshot(
                session_snapshot,
                message_id=message_id,
                source=source,
                task_id=task_id,
                stream_generation=stream_generation,
            )
            turn, tracker = start_detached_chat_turn(
                prepare_db,
                turn=turn,
                lifecycle=lifecycle,
                input_json=input_json(turn),
            )
            prepared = prepare(prepare_db, turn)
            mark_detached_chat_turn_running(
                prepare_db,
                turn=turn,
                tracker=tracker,
                lifecycle=lifecycle,
            )

        # Preparation can outlive the lease window. Renew immediately before
        # the provider boundary so a reclaimed attempt cannot incur duplicate work.
        require_current_chat_lease(ensure_lease)
        partial_writer = DurableChatPartialWriter(
            session_factory=session_factory,
            message_id=message_id,
            stream_generation=stream_generation,
        )
        external_start = perf_counter()
        executed = await execute(prepared, turn, partial_writer)
        external_ms = (perf_counter() - external_start) * 1000
        try:
            record_usage(
                raw_result(executed),
                turn.usage_snapshot,
                turn.session_id,
                turn.message_id,
                lifecycle.usage_context,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Chat usage telemetry failed; continuing with terminal persistence",
                exc_info=True,
                extra={"session_id": turn.session_id, "message_id": turn.message_id},
            )

        require_current_chat_lease(ensure_lease)
        persistence_start = perf_counter()
        with session_factory() as persist_db:
            persisted_session = (
                persist_db.query(ChatSession).filter(ChatSession.id == turn.session_id).first()
            )
            if persisted_session is None:
                raise RuntimeError(f"Chat session {turn.session_id} disappeared before persistence")
            output_json = persist(persist_db, executed, turn)
            if after_persist is not None:
                after_persist(persist_db, persisted_session)
            complete_detached_chat_turn(
                persist_db,
                session=persisted_session,
                turn=turn,
                tracker=tracker,
                lifecycle=lifecycle,
                output_json=output_json,
            )
        persistence_ms = (perf_counter() - persistence_start) * 1000
        return build_result(QueuedChatTurnOutcome.COMPLETED)
    except ChatTurnOwnershipLost:
        return cancel_result("Chat turn cancelled after queue ownership changed")
    except ChatTurnLeaseCheckError:
        _cancel_detached_attempt(
            session_factory=session_factory,
            tracker=tracker,
            note="Chat turn cancelled because queue ownership could not be verified",
            error_type="LeaseCheckError",
            error_message="Queue lease ownership could not be verified",
        )
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Queued chat turn execution failed",
            extra={
                "component": "queued_chat_turn",
                "operation": "execute_queued_chat_turn",
                "session_id": session_id,
                "message_id": message_id,
                "task_id": task_id,
                "context_data": {"failure_class": type(exc).__name__},
            },
        )
        try:
            require_current_chat_lease(ensure_lease)
        except ChatTurnOwnershipLost:
            return cancel_result("Failed chat turn cancelled after queue ownership changed")
        except ChatTurnLeaseCheckError:
            _cancel_detached_attempt(
                session_factory=session_factory,
                tracker=tracker,
                note="Failed chat turn cancelled because queue ownership could not be verified",
                error_type="LeaseCheckError",
                error_message="Queue lease ownership could not be verified",
            )
            raise

        try:
            with session_factory() as fail_db:
                initialize_chat_stream_attempt(
                    fail_db,
                    message_id=message_id,
                    stream_generation=stream_generation,
                )
                mark_message_failed(
                    fail_db,
                    message_id,
                    public_failure_message,
                    stream_generation,
                )
                if tracker.task_id is None:
                    fail_db.commit()
                else:
                    tracker.failed(
                        fail_db,
                        note=lifecycle.failed_note,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
        except ChatTurnOwnershipLost:
            return cancel_result("Failed chat turn superseded before failure persistence")
        return build_result(QueuedChatTurnOutcome.FAILED)
    finally:
        if prepared is not None:
            cleanup(prepared)
