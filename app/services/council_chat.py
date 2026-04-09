"""Helpers for council chat orchestration."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from sqlalchemy.orm import Session, sessionmaker

from app.models.chat_message_metadata import ChatMessageRenderMetadata, CouncilCandidate
from app.models.schema import ChatMessage, ChatSession, Content
from app.models.user import (
    MIN_COUNCIL_EXPERTS,
    CouncilPersonaConfig,
    User,
    resolve_user_council_personas,
)
from app.services.chat_agent import build_article_context, run_chat_turn, save_messages

DISALLOWED_COUNCIL_SESSION_TYPES = {"deep_research"}


@dataclass
class CouncilStartResult:
    """Persisted result of starting council mode on a parent session."""

    parent_session: ChatSession
    child_sessions: list[ChatSession]
    council_message: ChatMessage


@dataclass
class CouncilBranchExecutionResult:
    """Persisted result for one council branch turn."""

    child_session_id: int
    persona_id: str
    persona_name: str
    assistant_text: str


def validate_council_parent_session(session: ChatSession) -> None:
    """Raise a value error when the session cannot be used for council mode."""

    if session.is_hidden_from_history:
        raise ValueError("Council branches cannot start nested council sessions")
    if session.council_mode:
        raise ValueError("Council mode already started for this chat")
    if session.session_type in DISALLOWED_COUNCIL_SESSION_TYPES:
        raise ValueError("Council mode is unavailable for this chat type")


def get_parent_council_candidates(
    db: Session, parent_session: ChatSession
) -> list[CouncilCandidate]:
    """Return the council candidates stored on the parent council row."""

    if not parent_session.council_message_id:
        return []
    db_message = (
        db.query(ChatMessage).filter(ChatMessage.id == parent_session.council_message_id).first()
    )
    if not db_message or not isinstance(db_message.render_metadata, dict):
        return []
    try:
        render_metadata = ChatMessageRenderMetadata.model_validate(db_message.render_metadata)
    except Exception:  # noqa: BLE001
        return []
    return sorted(render_metadata.council_candidates, key=lambda candidate: candidate.order)


def _build_impersonation_prompt(persona: CouncilPersonaConfig) -> str:
    """Generate a rich impersonation prompt for a real-person expert."""

    name = persona.display_name
    return "\n".join(
        [
            f"You are {name}.",
            "",
            f"Respond to the content exactly as {name} would — drawing on their known "
            "intellectual frameworks, public writings, talks, interviews, and characteristic "
            "reasoning style.",
            "",
            "Guidelines:",
            (
                f"- Embody {name}'s actual perspective and voice, "
                "not a generic summary of their views."
            ),
            "- Use their vocabulary, rhetorical patterns, and level of detail.",
            (f"- If {name} has strong opinions on the topic, express those views directly."),
            (
                "- If the topic falls outside their known expertise, "
                "reason from their established frameworks and say so briefly."
            ),
            "- Write in first person. Stay in character throughout.",
            (
                f"- Prioritize what {name} would actually find interesting "
                "or important about this topic."
            ),
            (
                f"- Do NOT open with 'As {name}...' or any self-referential "
                "preamble. Just respond as they would."
            ),
        ]
    )


def build_child_context_snapshot(
    db: Session,
    *,
    parent_session: ChatSession,
    persona: CouncilPersonaConfig,
) -> str:
    """Build a council child context snapshot with expert impersonation prompt."""

    context_sections: list[str] = []
    if parent_session.context_snapshot:
        context_sections.append(parent_session.context_snapshot.strip())
    elif parent_session.content_id:
        content = db.query(Content).filter(Content.id == parent_session.content_id).first()
        if content is not None:
            content_context = build_article_context(db, content, include_full_text=True)
            if content_context:
                context_sections.append(content_context.strip())

    context_sections.append(_build_impersonation_prompt(persona))
    context_sections.append(
        "\n".join(
            [
                "Response Style:",
                "- Keep responses concise by default.",
                (
                    "- Prefer 2-4 short bullets or at most 2 short paragraphs "
                    "unless the user explicitly asks for depth."
                ),
                "- Lead with the most important insight instead of a long preamble.",
                "- Focus on what matters, what is weak or missing, and what follows.",
            ]
        )
    )
    return "\n\n".join(section for section in context_sections if section.strip()).strip()


def clone_session_messages(
    db: Session,
    *,
    source_session_id: int,
    target_session_id: int,
) -> None:
    """Clone persisted chat history from one session into another."""

    source_messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == source_session_id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
        .all()
    )
    for source in source_messages:
        db.add(
            ChatMessage(
                session_id=target_session_id,
                message_list=source.message_list,
                render_metadata=json.loads(json.dumps(source.render_metadata))
                if source.render_metadata is not None
                else None,
                created_at=source.created_at,
                status=source.status,
                error=source.error,
            )
        )


def build_council_branch_sessions(
    db: Session,
    *,
    parent_session: ChatSession,
    user: User,
) -> list[ChatSession]:
    """Create hidden council child sessions for the supplied parent session."""

    child_sessions: list[ChatSession] = []
    personas = resolve_user_council_personas(user)
    for persona in personas:
        child_session = ChatSession(
            user_id=parent_session.user_id,
            content_id=parent_session.content_id,
            parent_session_id=parent_session.id,
            title=parent_session.title,
            session_type=parent_session.session_type,
            topic=parent_session.topic,
            context_snapshot=build_child_context_snapshot(
                db,
                parent_session=parent_session,
                persona=persona,
            ),
            council_persona_id=persona.id,
            council_persona_name=persona.display_name,
            council_persona_prompt=_build_impersonation_prompt(persona),
            council_mode=False,
            is_hidden_from_history=True,
            llm_model=parent_session.llm_model,
            llm_provider=parent_session.llm_provider,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_message_at=parent_session.last_message_at,
        )
        db.add(child_session)
        db.flush()
        clone_session_messages(
            db,
            source_session_id=parent_session.id,
            target_session_id=child_session.id,
        )
        child_sessions.append(child_session)

    db.commit()
    for child_session in child_sessions:
        db.refresh(child_session)
    return child_sessions


def _extract_user_prompt(messages: list[ModelMessage]) -> str | None:
    for model_message in messages:
        if isinstance(model_message, ModelRequest):
            for part in model_message.parts:
                if isinstance(part, UserPromptPart):
                    return str(part.content)
    return None


def _extract_latest_assistant_text(messages: list[ModelMessage]) -> str | None:
    for model_message in reversed(messages):
        if isinstance(model_message, ModelResponse):
            for part in model_message.parts:
                if isinstance(part, TextPart) and part.content:
                    return part.content
    return None


def _update_council_message_content(
    council_message: ChatMessage,
    *,
    user_prompt: str,
    assistant_text: str,
) -> None:
    council_message.message_list = ModelMessagesTypeAdapter.dump_json(
        [
            ModelRequest(parts=[UserPromptPart(content=user_prompt)]),
            ModelResponse(parts=[TextPart(content=assistant_text)]),
        ]
    ).decode("utf-8")


async def _run_council_branch_turn(
    *,
    session_factory: sessionmaker[Session],
    child_session_id: int,
    user_prompt: str,
) -> CouncilBranchExecutionResult:
    """Run one council branch turn in its own database session."""

    with session_factory() as branch_db:
        child_session = (
            branch_db.query(ChatSession).filter(ChatSession.id == child_session_id).first()
        )
        if child_session is None:
            raise ValueError("Council branch session not found")

        result = await run_chat_turn(
            branch_db,
            child_session,
            user_prompt,
            source="council",
        )
        branch_message = (
            branch_db.query(ChatMessage)
            .filter(ChatMessage.session_id == child_session.id)
            .order_by(ChatMessage.id.desc())
            .first()
        )
        if branch_message is None:
            raise ValueError("Council branch response was not persisted")

        assistant_text = result.output_text.strip() or _extract_latest_assistant_text(
            result.new_messages
        )
        if not assistant_text:
            raise ValueError("Council branch response was empty")

        branch_timestamp = datetime.now(UTC)
        (
            branch_db.query(ChatSession)
            .filter(ChatSession.id == child_session.id)
            .update(
                {
                    ChatSession.branch_start_message_id: branch_message.id,
                    ChatSession.updated_at: branch_timestamp,
                    ChatSession.last_message_at: branch_timestamp,
                },
                synchronize_session=False,
            )
        )
        branch_db.commit()

        return CouncilBranchExecutionResult(
            child_session_id=child_session.id,
            persona_id=child_session.council_persona_id or "persona",
            persona_name=child_session.council_persona_name or "Persona",
            assistant_text=assistant_text,
        )


async def start_council_chat(
    db: Session,
    *,
    parent_session: ChatSession,
    user: User,
    user_prompt: str,
) -> CouncilStartResult:
    """Start council mode for a parent session and persist the council row."""

    validate_council_parent_session(parent_session)
    personas = resolve_user_council_personas(user)
    if len(personas) < MIN_COUNCIL_EXPERTS:
        raise ValueError("Add at least two experts in Settings before using the council")
    child_sessions = build_council_branch_sessions(
        db,
        parent_session=parent_session,
        user=user,
    )
    branch_session_factory = sessionmaker(
        bind=db.get_bind(),
        autocommit=False,
        autoflush=False,
    )

    branch_results = await asyncio.gather(
        *[
            _run_council_branch_turn(
                session_factory=branch_session_factory,
                child_session_id=child_session.id,
                user_prompt=user_prompt,
            )
            for child_session in child_sessions
        ]
    )
    db.expire_all()

    candidates: list[CouncilCandidate] = []
    active_child_session_id: int | None = None
    active_child_assistant_text = ""

    for order, branch_result in enumerate(branch_results):
        candidates.append(
            CouncilCandidate(
                persona_id=branch_result.persona_id or f"persona_{order}",
                persona_name=branch_result.persona_name or f"Persona {order + 1}",
                child_session_id=branch_result.child_session_id,
                content=branch_result.assistant_text,
                status="completed",
                order=order,
            )
        )
        if active_child_session_id is None:
            active_child_session_id = branch_result.child_session_id
            active_child_assistant_text = branch_result.assistant_text

    council_message = save_messages(
        db,
        parent_session.id,
        [
            ModelRequest(parts=[UserPromptPart(content=user_prompt)]),
            ModelResponse(parts=[TextPart(content=active_child_assistant_text)]),
        ],
        display_user_prompt=user_prompt,
        render_metadata=ChatMessageRenderMetadata(
            council_candidates=candidates,
            active_council_child_session_id=active_child_session_id,
        ),
    )

    parent_session.council_mode = True
    parent_session.active_child_session_id = active_child_session_id
    parent_session.council_message_id = council_message.id
    parent_session.updated_at = datetime.now(UTC)
    parent_session.last_message_at = datetime.now(UTC)
    db.commit()
    db.refresh(parent_session)

    return CouncilStartResult(
        parent_session=parent_session,
        child_sessions=child_sessions,
        council_message=council_message,
    )


def select_council_branch(
    db: Session,
    *,
    parent_session: ChatSession,
    child_session_id: int,
) -> ChatSession:
    """Switch the active branch for a council parent session."""

    child_session = (
        db.query(ChatSession)
        .filter(
            ChatSession.id == child_session_id,
            ChatSession.parent_session_id == parent_session.id,
            ChatSession.is_hidden_from_history == True,  # noqa: E712
        )
        .first()
    )
    if child_session is None:
        raise ValueError("Council branch not found")
    if parent_session.active_child_session_id == child_session.id:
        return child_session

    parent_session.active_child_session_id = child_session.id
    parent_session.updated_at = datetime.now(UTC)
    if child_session.last_message_at:
        parent_session.last_message_at = child_session.last_message_at

    if parent_session.council_message_id:
        council_message = (
            db.query(ChatMessage)
            .filter(ChatMessage.id == parent_session.council_message_id)
            .first()
        )
        if council_message is not None:
            candidates = get_parent_council_candidates(db, parent_session)
            active_candidate = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.child_session_id == child_session.id
                ),
                None,
            )
            if active_candidate is not None:
                _update_council_message_content(
                    council_message,
                    user_prompt=_extract_user_prompt(
                        ModelMessagesTypeAdapter.validate_json(council_message.message_list)
                    )
                    or "",
                    assistant_text=active_candidate.content,
                )
                council_message.render_metadata = ChatMessageRenderMetadata(
                    council_candidates=candidates,
                    active_council_child_session_id=child_session.id,
                ).model_dump(mode="json")

    db.commit()
    db.refresh(parent_session)
    return child_session
