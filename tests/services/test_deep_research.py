from __future__ import annotations

from contextlib import nullcontext

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.models.contracts import MessageProcessingStatus
from app.models.db import ChatMessage, ChatSession
from app.services import deep_research


@pytest.mark.asyncio
async def test_process_deep_research_releases_db_session_during_llm(
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    chat_session = ChatSession(
        user_id=test_user.id,
        title="Deep Research",
        session_type="deep_research",
        context_snapshot="Detached context",
        llm_provider="deep_research",
        llm_model=deep_research.DEEP_RESEARCH_MODEL,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)

    message = ChatMessage(
        session_id=chat_session.id,
        message_list="[]",
        status=MessageProcessingStatus.PROCESSING.value,
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)

    real_factory = sessionmaker(bind=db_session.get_bind())

    class TrackingSession:
        active_count = 0

        def __init__(self) -> None:
            self._session = real_factory()
            self._closed = False
            type(self).active_count += 1

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            self.close()

        def close(self) -> None:
            if self._closed:
                return
            self._closed = True
            type(self).active_count -= 1
            self._session.close()

        def __getattr__(self, name: str):
            return getattr(self._session, name)

    class FakeDeepResearchClient:
        async def start_research(self, user_prompt: str, context: str | None) -> str:
            assert user_prompt == "Find the latest context"
            assert context == "Detached context"
            assert TrackingSession.active_count == 0
            return "response-1"

        async def wait_for_completion(self, response_id: str) -> deep_research.DeepResearchResult:
            assert response_id == "response-1"
            assert TrackingSession.active_count == 0
            return deep_research.DeepResearchResult(
                response_id=response_id,
                status="completed",
                output_text="Research answer",
                sources=None,
                usage=None,
                error=None,
            )

    monkeypatch.setattr("app.core.db.get_session_factory", lambda: TrackingSession)
    monkeypatch.setattr(deep_research, "get_deep_research_client", lambda: FakeDeepResearchClient())
    monkeypatch.setattr(deep_research, "langfuse_trace_context", lambda **_kwargs: nullcontext())

    assert chat_session.id is not None
    assert message.id is not None
    await deep_research.process_deep_research_message(
        chat_session.id,
        message.id,
        "Find the latest context",
    )

    assert TrackingSession.active_count == 0
    db_session.expire_all()
    persisted = db_session.query(ChatMessage).filter(ChatMessage.id == message.id).one()
    assert persisted.status == MessageProcessingStatus.COMPLETED.value
    assert persisted.message_list is not None
    assert "Research answer" in persisted.message_list
