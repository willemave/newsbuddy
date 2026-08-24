from sqlalchemy.orm import Session

from app.models.contracts import TaskStatus
from app.models.db import ProcessingTask
from app.services.agent_data_events import enqueue_agent_data_sync


def test_identical_agent_data_events_follow_processing_sync_with_one_successor(
    db_session: Session,
    test_user,
) -> None:
    assert test_user.id is not None

    pending_id = enqueue_agent_data_sync(
        db_session,
        user_id=test_user.id,
        chat_session_ids=(42,),
    )
    assert (
        enqueue_agent_data_sync(
            db_session,
            user_id=test_user.id,
            chat_session_ids=(42,),
        )
        == pending_id
    )

    processing = db_session.get(ProcessingTask, pending_id)
    assert processing is not None
    base_key = str(processing.dedupe_key)
    processing.status = TaskStatus.PROCESSING.value
    db_session.flush()

    successor_id = enqueue_agent_data_sync(
        db_session,
        user_id=test_user.id,
        chat_session_ids=(42,),
    )
    assert successor_id != pending_id
    successor = db_session.get(ProcessingTask, successor_id)
    assert successor is not None
    assert successor.status == TaskStatus.PENDING.value
    assert successor.dedupe_key == f"{base_key}|after:{pending_id}"
    assert (
        enqueue_agent_data_sync(
            db_session,
            user_id=test_user.id,
            chat_session_ids=(42,),
        )
        == successor_id
    )
