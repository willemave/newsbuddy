"""Live eval harness for assistant action traces."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import get_settings
from app.models.chat_message_metadata import AssistantFeedOption
from app.models.contracts import TaskStatus, TaskType
from app.models.internal.assistant import AssistantScreenContext
from app.models.schema import (
    Content,
    ContentKnowledgeSave,
    NewsDigest,
    NewsDigestBullet,
    ProcessingTask,
    UserScraperConfig,
)
from app.models.user import User
from app.pipeline.handlers.analyze_url import AnalyzeUrlHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.assistant_router import (
    AssistantDeps,
    _extract_render_metadata,
    build_screen_context_snapshot,
    create_assistant_session,
    run_assistant_turn_sync,
)
from app.services.chat_agent import load_message_history
from app.services.llm_models import (
    DEFAULT_MODEL,
    build_pydantic_model,
    resolve_effective_api_key,
    resolve_model_provider,
)
from app.services.queue import QueueService
from app.testing.postgres_harness import TemporaryPostgresHarness, create_temporary_postgres_harness

DEFAULT_JUDGE_MODEL_SPEC = "openai:gpt-5.4"
DEFAULT_SCREEN_CONTEXT = AssistantScreenContext(
    screen_type="assistant_quick",
    screen_title="Quick Assistant",
)


class AssistantEvalDefaults(BaseModel):
    """Defaults shared across all eval cases."""

    model_spec: str = Field(default=DEFAULT_MODEL, min_length=1)
    judge_model_spec: str = Field(default=DEFAULT_JUDGE_MODEL_SPEC, min_length=1)
    screen_context: AssistantScreenContext = Field(
        default_factory=lambda: AssistantScreenContext.model_validate(
            DEFAULT_SCREEN_CONTEXT.model_dump(mode="python")
        )
    )


class AssistantEvalCase(BaseModel):
    """Single assistant action eval case."""

    id: str = Field(..., min_length=1, max_length=200)
    query: str = Field(..., min_length=1)
    expected_outcome: str = Field(..., min_length=1)
    expected_feed_options: bool = False
    screen_context: AssistantScreenContext | None = None
    seed_data: AssistantEvalSeedData = Field(default_factory=lambda: AssistantEvalSeedData())


class AssistantEvalSeedNewsDigestBullet(BaseModel):
    """News digest bullet fixture seeded for one eval case."""

    topic: str = Field(..., min_length=1, max_length=240)
    details: str = Field(..., min_length=1)


class AssistantEvalSeedNewsDigest(BaseModel):
    """News digest fixture seeded for one eval case."""

    title: str = Field(..., min_length=1, max_length=240)
    summary: str = Field(..., min_length=1)
    bullets: list[AssistantEvalSeedNewsDigestBullet] = Field(default_factory=list)
    source_count: int = Field(default=0, ge=0)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    trigger_reason: str = Field(default="assistant_eval", min_length=1, max_length=64)
    llm_model: str = Field(default="eval-seed", min_length=1, max_length=120)
    read_at: datetime | None = None
    window_start_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    window_end_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


class AssistantEvalSeedData(BaseModel):
    """Fixture data seeded before an eval case runs."""

    news_digests: list[AssistantEvalSeedNewsDigest] = Field(default_factory=list)
    favorites: list[AssistantEvalSeedFavorite] = Field(default_factory=list)


class AssistantEvalSeedFavorite(BaseModel):
    """Favorited content fixture seeded for one eval case."""

    url: str = Field(..., min_length=1, max_length=2048)
    title: str = Field(..., min_length=1, max_length=500)
    source: str | None = Field(default=None, max_length=100)
    content_type: str = Field(default="article", min_length=1, max_length=20)
    status: str = Field(default="completed", min_length=1, max_length=20)
    summary: str | None = Field(default=None, min_length=1)
    transcript_excerpt: str | None = Field(default=None, min_length=1)


class AssistantEvalSuite(BaseModel):
    """YAML-backed assistant eval suite."""

    suite: str = Field(..., min_length=1, max_length=200)
    defaults: AssistantEvalDefaults = Field(default_factory=AssistantEvalDefaults)
    cases: list[AssistantEvalCase]

    model_config = ConfigDict(extra="forbid")

    @field_validator("cases")
    @classmethod
    def validate_cases(cls, value: list[AssistantEvalCase]) -> list[AssistantEvalCase]:
        """Ensure the suite contains unique case IDs."""
        seen: set[str] = set()
        for case in value:
            if case.id in seen:
                raise ValueError(f"Duplicate case id: {case.id}")
            seen.add(case.id)
        return value


class AssistantTraceEvent(BaseModel):
    """Normalized assistant trace event for judge payloads."""

    kind: Literal["tool_call", "tool_return", "assistant_text"]
    tool_name: str | None = None
    tool_call_id: str | None = None
    args: dict[str, Any] | str | None = None
    content: str | None = None


class AssistantTrace(BaseModel):
    """Judge-facing trace payload."""

    query: str
    model_spec: str
    final_assistant_text: str
    events: list[AssistantTraceEvent]


class AssistantJudgeVerdict(BaseModel):
    """Structured judge verdict."""

    passed: bool
    score: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., min_length=1)


class AssistantEvalDebugState(BaseModel):
    """Optional post-run state for human-readable reports."""

    subscriptions: list[dict[str, Any]] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    feed_options: list[dict[str, Any]] = Field(default_factory=list)


class AssistantEvalResult(BaseModel):
    """Result of one assistant eval case."""

    suite: str
    case_id: str
    model_spec: str
    judge_model_spec: str
    expected_outcome: str
    passed: bool
    score: float | None = None
    reasoning: str | None = None
    assistant_text: str | None = None
    trace: AssistantTrace | None = None
    debug_state: AssistantEvalDebugState = Field(default_factory=AssistantEvalDebugState)
    error: str | None = None


class AssistantEvalReport(BaseModel):
    """Whole-run assistant eval report."""

    suite: str
    results: list[AssistantEvalResult]

@dataclass
class _EvalQueueGateway:
    """Minimal queue gateway for in-process eval task execution."""

    enqueued_tasks: list[dict[str, Any]]

    def enqueue(
        self,
        task_type: TaskType,
        *,
        content_id: int | None = None,
        payload: dict[str, Any] | None = None,
        queue_name: str | None = None,
        dedupe: bool | None = None,
    ) -> int:
        """Record downstream enqueues for debug visibility."""

        self.enqueued_tasks.append(
            {
                "task_type": task_type.value,
                "content_id": content_id,
                "payload": payload or {},
                "queue_name": queue_name,
                "dedupe": dedupe,
            }
        )
        return len(self.enqueued_tasks)


def create_eval_harness() -> TemporaryPostgresHarness:
    """Create an isolated PostgreSQL eval harness."""
    return create_temporary_postgres_harness()


def load_assistant_eval_suite(path: str | Path) -> AssistantEvalSuite:
    """Load an assistant eval suite from YAML."""

    with open(path, encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return AssistantEvalSuite.model_validate(payload)


def _resolve_screen_context(
    defaults: AssistantEvalDefaults,
    case: AssistantEvalCase,
) -> AssistantScreenContext:
    """Resolve the screen context for one eval case."""

    return case.screen_context or defaults.screen_context


def _seed_case_data(
    db: Session,
    *,
    user_id: int,
    seed_data: AssistantEvalSeedData,
) -> None:
    """Seed fixture data required by an eval case."""

    for digest in seed_data.news_digests:
        row = NewsDigest(
            user_id=user_id,
            timezone=digest.timezone,
            window_start_at=digest.window_start_at,
            window_end_at=digest.window_end_at,
            title=digest.title,
            summary=digest.summary,
            source_count=digest.source_count,
            group_count=len(digest.bullets),
            embedding_model="eval-seed",
            llm_model=digest.llm_model,
            pipeline_version="assistant-eval",
            trigger_reason=digest.trigger_reason,
            generated_at=digest.generated_at,
            read_at=digest.read_at,
            build_metadata={},
        )
        db.add(row)
        db.flush()
        for position, bullet in enumerate(digest.bullets, start=1):
            db.add(
                NewsDigestBullet(
                    digest_id=row.id,
                    position=position,
                    topic=bullet.topic,
                    details=bullet.details,
                    source_count=0,
                )
            )
    for favorite in seed_data.favorites:
        content_metadata: dict[str, Any] = {}
        if favorite.summary:
            content_metadata["summary"] = {"overview": favorite.summary}
        if favorite.transcript_excerpt:
            content_metadata["transcript"] = favorite.transcript_excerpt

        content = Content(
            content_type=favorite.content_type,
            url=favorite.url,
            title=favorite.title,
            source=favorite.source,
            status=favorite.status,
            content_metadata=content_metadata,
        )
        db.add(content)
        db.flush()
        db.add(ContentKnowledgeSave(user_id=user_id, content_id=int(content.id)))

    if seed_data.news_digests or seed_data.favorites:
        db.commit()


def _serialize_event_payload(value: Any) -> str:
    """Serialize arbitrary tool payloads into compact trace text."""

    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return str(value)


@contextmanager
def _db_context_factory(session_factory: sessionmaker):
    """Yield a fresh database session for task-context callbacks."""

    with session_factory() as db:
        yield db


def _run_pending_analyze_url_tasks(session_factory: sessionmaker) -> None:
    """Execute queued ANALYZE_URL tasks inside the eval harness."""

    queue_gateway = _EvalQueueGateway(enqueued_tasks=[])
    context = TaskContext(
        queue_service=QueueService(),
        settings=get_settings(),
        llm_service=None,
        worker_id="assistant-eval",
        queue_gateway=queue_gateway,
        db_factory=lambda: _db_context_factory(session_factory),
    )
    handler = AnalyzeUrlHandler()

    with session_factory() as db:
        pending_task_ids = [
            int(task.id)
            for task in (
                db.query(ProcessingTask)
                .filter(ProcessingTask.task_type == TaskType.ANALYZE_URL.value)
                .filter(ProcessingTask.status == TaskStatus.PENDING.value)
                .order_by(ProcessingTask.id.asc())
                .all()
            )
        ]

    for task_id in pending_task_ids:
        with session_factory() as db:
            task_row = db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
            if task_row is None:
                continue
            task = TaskEnvelope(
                id=task_row.id,
                task_type=TaskType.ANALYZE_URL,
                content_id=task_row.content_id,
                payload=dict(task_row.payload or {}),
                retry_count=int(task_row.retry_count or 0),
                status=task_row.status,
                queue_name=task_row.queue_name,
                created_at=task_row.created_at,
                started_at=task_row.started_at,
            )

        result = handler.handle(task, context)

        with session_factory() as db:
            stored_task = db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
            if stored_task is None:
                continue
            stored_task.status = (
                TaskStatus.COMPLETED.value if result.success else TaskStatus.FAILED.value
            )
            stored_task.error_message = result.error_message
            db.commit()


def build_assistant_trace(
    *,
    query: str,
    model_spec: str,
    messages: list[ModelMessage],
) -> AssistantTrace:
    """Convert pydantic-ai messages into a judge-facing trace."""

    events: list[AssistantTraceEvent] = []
    assistant_texts: list[str] = []

    for model_message in messages:
        if isinstance(model_message, ModelResponse):
            for part in model_message.parts:
                if isinstance(part, ToolCallPart):
                    args_value: dict[str, Any] | str | None
                    if isinstance(part.args, dict):
                        args_value = part.args
                    elif part.args is None:
                        args_value = None
                    else:
                        args_value = str(part.args)
                    events.append(
                        AssistantTraceEvent(
                            kind="tool_call",
                            tool_name=part.tool_name,
                            tool_call_id=part.tool_call_id,
                            args=args_value,
                        )
                    )
                elif isinstance(part, TextPart) and part.content:
                    assistant_texts.append(part.content)
                    events.append(
                        AssistantTraceEvent(
                            kind="assistant_text",
                            content=part.content,
                        )
                    )
        elif isinstance(model_message, ModelRequest):
            for part in model_message.parts:
                if isinstance(part, ToolReturnPart):
                    events.append(
                        AssistantTraceEvent(
                            kind="tool_return",
                            tool_name=part.tool_name,
                            tool_call_id=part.tool_call_id,
                            content=_serialize_event_payload(part.content),
                        )
                    )

    return AssistantTrace(
        query=query,
        model_spec=model_spec,
        final_assistant_text=assistant_texts[-1] if assistant_texts else "",
        events=events,
    )


def build_generic_judge_prompt(
    *,
    expected_outcome: str,
    trace: AssistantTrace,
) -> str:
    """Build the generic judge prompt for one eval case."""

    trace_json = json.dumps(trace.model_dump(mode="json"), indent=2, sort_keys=True)
    return (
        "You are grading whether an assistant satisfied an expected outcome.\n\n"
        f"Expected outcome:\n{expected_outcome.strip()}\n\n"
        "Observed execution trace:\n"
        f"{trace_json}\n\n"
        "Decide whether the assistant satisfied the expected outcome.\n"
        "Consider the final assistant response and the actions reflected in the trace.\n"
        "Return passed=true only when the overall behavior matches the expected outcome.\n"
        "If the trace shows the wrong target, a missing action, or inconsistent behavior, fail it."
    )


def judge_assistant_trace(
    *,
    expected_outcome: str,
    trace: AssistantTrace,
    judge_model_spec: str,
) -> AssistantJudgeVerdict:
    """Judge a trace against the expected outcome using a live LLM."""

    model, model_settings = build_pydantic_model(judge_model_spec)
    judge_agent: Agent[None, AssistantJudgeVerdict] = Agent(
        model,
        output_type=AssistantJudgeVerdict,
    )
    result = judge_agent.run_sync(
        build_generic_judge_prompt(expected_outcome=expected_outcome, trace=trace),
        model_settings=model_settings,
    )
    return result.output


def _build_debug_state(
    db_session: Session,
    *,
    user_id: int,
    feed_options: list[AssistantFeedOption] | None = None,
) -> AssistantEvalDebugState:
    """Capture compact post-run state for human-readable reports."""

    subscriptions = (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == user_id)
        .order_by(UserScraperConfig.created_at.asc())
        .all()
    )
    rows = [
        {
            "scraper_type": row.scraper_type,
            "display_name": row.display_name,
            "feed_url": row.feed_url,
            "is_active": row.is_active,
        }
        for row in subscriptions
    ]
    task_rows = [
        {
            "id": task.id,
            "task_type": task.task_type,
            "status": task.status,
            "content_id": task.content_id,
            "error_message": task.error_message,
            "payload": dict(task.payload or {}),
        }
        for task in db_session.query(ProcessingTask).order_by(ProcessingTask.id.asc()).all()
    ]
    return AssistantEvalDebugState(
        subscriptions=rows,
        tasks=task_rows,
        feed_options=[
            option.model_dump(mode="json") for option in (feed_options or [])
        ],
    )


def run_assistant_eval_case(
    *,
    suite_name: str,
    defaults: AssistantEvalDefaults,
    case: AssistantEvalCase,
) -> AssistantEvalResult:
    """Run one assistant eval case end-to-end in an isolated harness."""

    harness = create_eval_harness()
    session_factory = harness.session_factory
    model_spec = defaults.model_spec
    judge_model_spec = defaults.judge_model_spec
    screen_context = _resolve_screen_context(defaults, case)

    try:
        with session_factory() as db:
            user = User(
                apple_id=f"assistant-eval-{case.id}",
                email=f"assistant-eval-{case.id}@example.com",
                full_name="Assistant Eval User",
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            _seed_case_data(db, user_id=user.id, seed_data=case.seed_data)

            context_snapshot = build_screen_context_snapshot(
                db,
                user_id=user.id,
                screen_context=screen_context,
            )
            chat_session = create_assistant_session(
                db,
                user_id=user.id,
                context_snapshot=context_snapshot,
                screen_context=screen_context,
            )
            chat_session.llm_model = model_spec
            chat_session.llm_provider = resolve_model_provider(model_spec)
            db.commit()
            db.refresh(chat_session)

            history = load_message_history(db, chat_session.id)
            deps = AssistantDeps(
                user_id=user.id,
                session_id=chat_session.id,
                screen_context=screen_context,
                context_snapshot=context_snapshot,
                session_factory=session_factory,
            )
            provider_api_key = resolve_effective_api_key(
                db=db,
                user_id=user.id,
                model_spec=model_spec,
            )

            raw_result = run_assistant_turn_sync(
                model_spec,
                case.query,
                deps,
                history,
                provider_api_key=provider_api_key,
            )
            _run_pending_analyze_url_tasks(session_factory)
            render_metadata = _extract_render_metadata(raw_result.new_messages())
            feed_options = render_metadata.feed_options if render_metadata else []

            trace = build_assistant_trace(
                query=case.query,
                model_spec=model_spec,
                messages=raw_result.new_messages(),
            )
            verdict = judge_assistant_trace(
                expected_outcome=case.expected_outcome,
                trace=trace,
                judge_model_spec=judge_model_spec,
            )

            db.expire_all()
            debug_state = _build_debug_state(
                db,
                user_id=user.id,
                feed_options=feed_options,
            )
            passed = verdict.passed
            score = verdict.score
            reasoning = verdict.reasoning
            if case.expected_feed_options and not feed_options:
                passed = False
                score = 0.0
                requirement_reason = (
                    "Missing validated feed options in assistant render metadata."
                )
                reasoning = (
                    f"{reasoning} {requirement_reason}"
                    if reasoning
                    else requirement_reason
                )
            return AssistantEvalResult(
                suite=suite_name,
                case_id=case.id,
                model_spec=model_spec,
                judge_model_spec=judge_model_spec,
                expected_outcome=case.expected_outcome,
                passed=passed,
                score=score,
                reasoning=reasoning,
                assistant_text=trace.final_assistant_text,
                trace=trace,
                debug_state=debug_state,
            )
    except Exception as exc:  # noqa: BLE001
        return AssistantEvalResult(
            suite=suite_name,
            case_id=case.id,
            model_spec=model_spec,
            judge_model_spec=judge_model_spec,
            expected_outcome=case.expected_outcome,
            passed=False,
            error=str(exc),
        )
    finally:
        harness.close()


def run_assistant_eval_suite(
    suite: AssistantEvalSuite,
    *,
    case_id: str | None = None,
    model_spec: str | None = None,
    judge_model_spec: str | None = None,
) -> AssistantEvalReport:
    """Run all requested cases in a suite."""

    defaults = suite.defaults.model_copy(deep=True)
    if model_spec:
        defaults.model_spec = model_spec
    if judge_model_spec:
        defaults.judge_model_spec = judge_model_spec

    selected_cases = suite.cases
    if case_id:
        selected_cases = [case for case in suite.cases if case.id == case_id]
        if not selected_cases:
            raise ValueError(f"Unknown eval case: {case_id}")

    results = [
        run_assistant_eval_case(
            suite_name=suite.suite,
            defaults=defaults,
            case=case,
        )
        for case in selected_cases
    ]
    return AssistantEvalReport(suite=suite.suite, results=results)
