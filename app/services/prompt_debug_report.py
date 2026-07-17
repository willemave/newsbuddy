"""Build local prompt-debug reports from synced JSONL logs."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import get_settings
from app.models.db import Content
from app.services.content_analyzer import CONTENT_ANALYSIS_MODEL, CONTENT_ANALYZER_SYSTEM_PROMPT
from app.services.content_bodies import get_content_body_resolver
from app.services.llm_prompts import generate_summary_prompt
from app.services.llm_summarization import DEFAULT_SUMMARIZATION_MODELS
from app.services.prompt_library import render_prompt
from app.services.summarization_templates import resolve_summarization_prompt_route

FAILURE_LEVELS = {"ERROR", "CRITICAL"}
DEFAULT_COMPONENTS = ("summarization", "llm_summarization", "content_analyzer")
DEFAULT_DB_LABEL = "<from app settings>"
PROMPT_PREVIEW_LIMIT = 10_000


class SyncOptions(BaseModel):
    """Remote log sync options."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    remote_user: str = "willem"
    remote_host: str = "news-app-server"
    remote_logs_dir: str = "/data/logs"
    remote_app_dir: str = "/opt/news_app"
    local_logs_dir: Path = Field(default_factory=lambda: Path("./logs_from_server"))


class PromptReportOptions(BaseModel):
    """Configuration for local prompt debug report generation."""

    model_config = ConfigDict(extra="forbid")

    logs_dir: Path = Field(default_factory=lambda: Path("./logs_from_server"))
    db_url: str | None = None
    hours: int = Field(default=24, ge=1, le=24 * 90)
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=200, ge=1, le=5_000)
    components: tuple[str, ...] = DEFAULT_COMPONENTS
    include_json: bool = False
    output_dir: Path = Field(default_factory=lambda: Path("./outputs"))
    sync: SyncOptions = Field(default_factory=SyncOptions)

    @field_validator("components")
    @classmethod
    def validate_components(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Normalize component filters."""
        cleaned = tuple(component.strip().lower() for component in value if component.strip())
        if not cleaned:
            msg = "At least one component must be configured"
            raise ValueError(msg)
        return cleaned

    @model_validator(mode="after")
    def validate_date_window(self) -> PromptReportOptions:
        """Ensure optional date window is coherent."""
        if self.since and self.until and self.since > self.until:
            msg = "`since` must be earlier than or equal to `until`"
            raise ValueError(msg)
        return self


class LogRecord(BaseModel):
    """Structured representation of a single JSONL log line."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime | None
    level: str
    component: str
    operation: str | None
    item_id: str | None
    error_type: str | None
    error_message: str | None
    message: str | None
    context_data: dict[str, Any]
    source_file: str


class FailureRecord(BaseModel):
    """Normalized failure row for prompt reconstruction."""

    model_config = ConfigDict(extra="forbid")

    phase: Literal["summarize", "analyze_url", "unknown"]
    timestamp: datetime | None
    component: str
    operation: str | None
    source_file: str
    item_id: str | None
    content_id: int | None
    url: str | None
    model: str | None
    error_type: str | None
    error_message: str
    context_data: dict[str, Any]


class PromptSnapshot(BaseModel):
    """Reconstructed prompt payload for a single failure."""

    model_config = ConfigDict(extra="forbid")

    phase: Literal["summarize", "analyze_url", "unknown"]
    reconstruction_quality: Literal["full", "partial", "insufficient-context"]
    timestamp: datetime | None
    source_file: str | None
    component: str
    operation: str | None
    content_id: int | None
    url: str | None
    model: str | None
    error_type: str | None
    error_message: str
    system_prompt: str | None
    user_prompt: str | None
    notes: list[str] = Field(default_factory=list)


class PromptDebugReport(BaseModel):
    """Complete prompt-debug report payload."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    logs_dir: str
    db_url: str
    window_hours: int
    window_start: datetime | None = None
    window_end: datetime | None = None
    total_records_scanned: int
    total_failures: int
    by_phase: dict[str, int]
    by_component: dict[str, int]
    by_model: dict[str, int]
    snapshots: list[PromptSnapshot]


def run_remote_sync(options: SyncOptions) -> None:
    """Sync logs from remote host to local directory using rsync."""
    if not options.enabled:
        return

    if shutil.which("ssh") is None or shutil.which("rsync") is None:
        msg = "Sync requested but missing required commands: ssh and rsync"
        raise RuntimeError(msg)

    remote = f"{options.remote_user}@{options.remote_host}"
    local_logs = options.local_logs_dir
    local_logs.mkdir(parents=True, exist_ok=True)

    _run_command(
        [
            "rsync",
            "-avz",
            f"{remote}:{options.remote_logs_dir.rstrip('/')}/",
            f"{local_logs}/",
        ]
    )

    local_service_logs = local_logs / "service_logs"
    if _remote_dir_exists(remote, "/var/log/news_app"):
        local_service_logs.mkdir(parents=True, exist_ok=True)
        _run_command(["rsync", "-avz", f"{remote}:/var/log/news_app/", f"{local_service_logs}/"])

    local_supervisor_logs = local_logs / "supervisor"
    if _remote_dir_exists(remote, "/var/log/newsly"):
        local_supervisor_logs.mkdir(parents=True, exist_ok=True)
        _run_command(["rsync", "-avz", f"{remote}:/var/log/newsly/", f"{local_supervisor_logs}/"])

    local_app_logs = local_logs / "app_logs"
    app_logs_path = f"{options.remote_app_dir.rstrip('/')}/logs"
    if _remote_dir_exists(remote, app_logs_path):
        local_app_logs.mkdir(parents=True, exist_ok=True)
        _run_command(["rsync", "-avz", f"{remote}:{app_logs_path}/", f"{local_app_logs}/"])


def collect_log_records(logs_dir: Path) -> list[LogRecord]:
    """Parse JSONL records under the provided log directory."""
    records: list[LogRecord] = []
    if not logs_dir.exists():
        return records

    for file_path in sorted(logs_dir.rglob("*.jsonl")):
        rel_path = str(file_path.relative_to(logs_dir))
        with file_path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record = _payload_to_log_record(payload, rel_path)
                if record is not None:
                    records.append(record)
    return records


def select_failure_records(
    records: list[LogRecord],
    options: PromptReportOptions,
    now: datetime | None = None,
) -> list[FailureRecord]:
    """Filter recent failures by component and classify prompt phase."""
    current_time = now or datetime.now(UTC)
    window_start, window_end = _resolve_window(options, current_time)
    allowed_components = set(options.components)
    requires_timestamp = options.since is not None or options.until is not None

    failures: list[FailureRecord] = []
    for record in records:
        record_component = (record.component or "").lower()
        if record_component not in allowed_components:
            continue

        if requires_timestamp and record.timestamp is None:
            continue
        if record.timestamp is not None:
            if window_start and record.timestamp < window_start:
                continue
            if window_end and record.timestamp > window_end:
                continue

        has_failure_signal = (
            (record.level or "").upper() in FAILURE_LEVELS
            or bool(record.error_message)
            or bool(record.error_type)
        )
        if not has_failure_signal:
            continue

        phase = _detect_phase(record_component, record.operation)
        content_id = _extract_content_id(record.item_id, record.context_data)
        failure = FailureRecord(
            phase=phase,
            timestamp=record.timestamp,
            component=record_component,
            operation=record.operation,
            source_file=record.source_file,
            item_id=record.item_id,
            content_id=content_id,
            url=_extract_url(record.context_data),
            model=_extract_model(record.context_data),
            error_type=record.error_type,
            error_message=record.error_message or record.message or "Unknown error",
            context_data=record.context_data,
        )
        failures.append(failure)

    failures.sort(
        key=lambda item: item.timestamp or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return failures[: options.limit]


def reconstruct_summarize_prompt(
    failure: FailureRecord,
    db_session_factory: sessionmaker[Session],
) -> PromptSnapshot:
    """Reconstruct summarize prompts from content metadata."""
    content_id = failure.content_id
    if content_id is None:
        return PromptSnapshot(
            phase="summarize",
            reconstruction_quality="insufficient-context",
            timestamp=failure.timestamp,
            source_file=failure.source_file,
            component=failure.component,
            operation=failure.operation,
            content_id=None,
            url=failure.url,
            model=failure.model,
            error_type=failure.error_type,
            error_message=failure.error_message,
            system_prompt=None,
            user_prompt=None,
            notes=["Missing content_id in failure record"],
        )

    with db_session_factory() as session:
        content = session.query(Content).filter(Content.id == content_id).first()
        if content is None:
            return PromptSnapshot(
                phase="summarize",
                reconstruction_quality="insufficient-context",
                timestamp=failure.timestamp,
                source_file=failure.source_file,
                component=failure.component,
                operation=failure.operation,
                content_id=content_id,
                url=failure.url,
                model=failure.model,
                error_type=failure.error_type,
                error_message=failure.error_message,
                system_prompt=None,
                user_prompt=None,
                notes=[f"Content id {content_id} not found in database"],
            )

        text_payload, prompt_type, max_bullets, max_quotes, notes = _extract_summarize_context(
            session,
            content,
        )
        if not text_payload:
            return PromptSnapshot(
                phase="summarize",
                reconstruction_quality="insufficient-context",
                timestamp=failure.timestamp,
                source_file=failure.source_file,
                component=failure.component,
                operation=failure.operation,
                content_id=content_id,
                url=str(content.url),
                model=(
                    failure.model or _default_model_for_summarize(content.content_type, prompt_type)
                ),
                error_type=failure.error_type,
                error_message=failure.error_message,
                system_prompt=None,
                user_prompt=None,
                notes=notes + ["No text payload available for prompt reconstruction"],
            )

        system_prompt, user_template = generate_summary_prompt(prompt_type, max_bullets, max_quotes)
        user_prompt = user_template.format(content=text_payload)
        notes.append(f"prompt_type={prompt_type}")
        notes.append(f"text_chars={len(text_payload)}")

        return PromptSnapshot(
            phase="summarize",
            reconstruction_quality="full",
            timestamp=failure.timestamp,
            source_file=failure.source_file,
            component=failure.component,
            operation=failure.operation,
            content_id=content_id,
            url=str(content.url),
            model=(
                failure.model or _default_model_for_summarize(content.content_type, prompt_type)
            ),
            error_type=failure.error_type,
            error_message=failure.error_message,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            notes=notes,
        )


def reconstruct_analyze_url_prompt(failure: FailureRecord) -> PromptSnapshot:
    """Reconstruct analyze-url prompt skeleton from available log context."""
    url = failure.url
    instruction = _extract_instruction(failure.context_data)
    detected_placeholder = "[reconstruction requires stored HTML snapshot]"
    content_placeholder = "[reconstruction requires stored page text snapshot]"

    notes = [
        "Reconstruction uses prompt skeleton only; analyzer does not persist full page text.",
    ]

    if not url:
        return PromptSnapshot(
            phase="analyze_url",
            reconstruction_quality="insufficient-context",
            timestamp=failure.timestamp,
            source_file=failure.source_file,
            component=failure.component,
            operation=failure.operation,
            content_id=failure.content_id,
            url=None,
            model=failure.model or CONTENT_ANALYSIS_MODEL,
            error_type=failure.error_type,
            error_message=failure.error_message,
            system_prompt=CONTENT_ANALYZER_SYSTEM_PROMPT,
            user_prompt=None,
            notes=notes + ["Missing URL in failure record context"],
        )

    user_prompt = render_prompt(
        "content/analyzer#reconstruction_user",
        url=url,
        instruction=instruction,
        detected_placeholder=detected_placeholder,
        content_placeholder=content_placeholder,
    )

    return PromptSnapshot(
        phase="analyze_url",
        reconstruction_quality="partial",
        timestamp=failure.timestamp,
        source_file=failure.source_file,
        component=failure.component,
        operation=failure.operation,
        content_id=failure.content_id,
        url=url,
        model=failure.model or CONTENT_ANALYSIS_MODEL,
        error_type=failure.error_type,
        error_message=failure.error_message,
        system_prompt=CONTENT_ANALYZER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        notes=notes,
    )


def build_prompt_debug_report(options: PromptReportOptions) -> PromptDebugReport:
    """Build prompt-debug report payload."""
    if options.sync.enabled:
        run_remote_sync(options.sync)

    db_url = options.db_url or str(get_settings().database_url)
    now = datetime.now(UTC)
    window_start, window_end = _resolve_window(options, now)
    records = collect_log_records(options.logs_dir)
    failures = select_failure_records(records, options, now=now)
    snapshots = reconstruct_prompt_snapshots(failures, db_url)

    phase_counts = Counter(snapshot.phase for snapshot in snapshots)
    component_counts = Counter(snapshot.component for snapshot in snapshots)
    model_counts = Counter(snapshot.model for snapshot in snapshots if snapshot.model)

    return PromptDebugReport(
        generated_at=datetime.now(UTC),
        logs_dir=str(options.logs_dir),
        db_url=options.db_url or DEFAULT_DB_LABEL,
        window_hours=options.hours,
        window_start=window_start,
        window_end=window_end,
        total_records_scanned=len(records),
        total_failures=len(failures),
        by_phase={str(key): value for key, value in phase_counts.items()},
        by_component={str(key): value for key, value in component_counts.items()},
        by_model={str(key): value for key, value in model_counts.items()},
        snapshots=snapshots,
    )


def write_report_files(
    report: PromptDebugReport,
    options: PromptReportOptions,
) -> tuple[Path, Path | None]:
    """Write Markdown (and optional JSON) report artifacts to disk."""
    options.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.strftime("%Y%m%d_%H%M%S")
    markdown_path = options.output_dir / f"prompt_debug_report_{stamp}.md"
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")

    json_path: Path | None = None
    if options.include_json:
        json_path = options.output_dir / f"prompt_debug_report_{stamp}.json"
        json_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")

    return markdown_path, json_path


def render_markdown_report(report: PromptDebugReport) -> str:
    """Render report payload as Markdown."""
    lines: list[str] = []
    lines.append("# Prompt Debug Report")
    lines.append("")
    lines.append(f"- Generated at: {report.generated_at.isoformat()}")
    lines.append(f"- Logs directory: `{report.logs_dir}`")
    lines.append(f"- DB source: `{report.db_url}`")
    lines.append(f"- Window start: `{_format_ts(report.window_start)}`")
    if report.window_end is not None:
        lines.append(f"- Window end: `{_format_ts(report.window_end)}`")
    else:
        lines.append("- Window end: `now`")
    lines.append(f"- Hours fallback: {report.window_hours}h")
    lines.append(f"- Records scanned: {report.total_records_scanned}")
    lines.append(f"- Failures selected: {report.total_failures}")
    lines.append("")

    lines.append("## Counts By Phase")
    if report.by_phase:
        for phase, count in sorted(report.by_phase.items()):
            lines.append(f"- {phase}: {count}")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## Counts By Component")
    if report.by_component:
        for component, count in sorted(report.by_component.items()):
            lines.append(f"- {component}: {count}")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## Counts By Model")
    if report.by_model:
        for model, count in sorted(report.by_model.items(), key=lambda pair: pair[1], reverse=True):
            lines.append(f"- {model}: {count}")
    else:
        lines.append("- unknown")
    lines.append("")

    lines.append("## Failures")
    if not report.snapshots:
        lines.append("- No failures found for selected filters.")
        return "\n".join(lines)

    for index, snapshot in enumerate(report.snapshots, start=1):
        lines.append("")
        lines.append(f"### {index}. {snapshot.phase} ({snapshot.reconstruction_quality})")
        lines.append(f"- Component: `{snapshot.component}`")
        lines.append(f"- Operation: `{snapshot.operation or 'unknown'}`")
        lines.append(f"- Timestamp: `{_format_ts(snapshot.timestamp)}`")
        lines.append(f"- Source file: `{snapshot.source_file or 'unknown'}`")
        content_id_label = snapshot.content_id if snapshot.content_id is not None else "n/a"
        lines.append(f"- Content ID: `{content_id_label}`")
        lines.append(f"- URL: `{snapshot.url or 'n/a'}`")
        lines.append(f"- Model: `{snapshot.model or 'unknown'}`")
        lines.append(f"- Error type: `{snapshot.error_type or 'unknown'}`")
        lines.append(f"- Error message: `{snapshot.error_message}`")

        if snapshot.notes:
            lines.append("- Notes:")
            for note in snapshot.notes:
                lines.append(f"  - {note}")

        lines.append("")
        lines.append("#### System Prompt")
        lines.append("```text")
        lines.append(_clip_for_display(snapshot.system_prompt))
        lines.append("```")
        lines.append("")
        lines.append("#### User Prompt")
        lines.append("```text")
        lines.append(_clip_for_display(snapshot.user_prompt))
        lines.append("```")

    return "\n".join(lines)


def reconstruct_prompt_snapshots(
    failures: list[FailureRecord],
    db_url: str,
) -> list[PromptSnapshot]:
    """Reconstruct prompt snapshots for all failure records."""
    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    snapshots: list[PromptSnapshot] = []
    try:
        for failure in failures:
            if failure.phase == "summarize":
                snapshots.append(reconstruct_summarize_prompt(failure, session_factory))
                continue
            if failure.phase == "analyze_url":
                snapshots.append(reconstruct_analyze_url_prompt(failure))
                continue
            snapshots.append(
                PromptSnapshot(
                    phase="unknown",
                    reconstruction_quality="insufficient-context",
                    timestamp=failure.timestamp,
                    source_file=failure.source_file,
                    component=failure.component,
                    operation=failure.operation,
                    content_id=failure.content_id,
                    url=failure.url,
                    model=failure.model,
                    error_type=failure.error_type,
                    error_message=failure.error_message,
                    system_prompt=None,
                    user_prompt=None,
                    notes=["No prompt reconstruction strategy for this phase"],
                )
            )
    finally:
        engine.dispose()

    return snapshots


def _payload_to_log_record(payload: dict[str, Any], source_file: str) -> LogRecord | None:
    """Convert a parsed JSON payload to a normalized log record."""
    context_value = payload.get("context_data")
    context_data = context_value if isinstance(context_value, dict) else {}
    timestamp = _parse_iso_timestamp(payload.get("timestamp"))

    component = str(payload.get("component") or payload.get("logger") or "unknown").lower()
    level = str(payload.get("level") or "INFO").upper()

    try:
        return LogRecord(
            timestamp=timestamp,
            level=level,
            component=component,
            operation=_coerce_optional_str(payload.get("operation")),
            item_id=_coerce_optional_str(payload.get("item_id")),
            error_type=_coerce_optional_str(payload.get("error_type")),
            error_message=_coerce_optional_str(payload.get("error_message")),
            message=_coerce_optional_str(payload.get("message")),
            context_data=context_data,
            source_file=source_file,
        )
    except Exception:
        return None


def _detect_phase(
    component: str,
    operation: str | None,
) -> Literal["summarize", "analyze_url", "unknown"]:
    """Classify log record phase for prompt reconstruction."""
    component_value = component.lower()
    operation_value = (operation or "").lower()

    if component_value in {"summarization", "llm_summarization"}:
        return "summarize"
    if "summar" in operation_value:
        return "summarize"

    if component_value == "content_analyzer":
        return "analyze_url"
    if operation_value in {"analyze_url", "parse_output", "fetch_page_content"}:
        return "analyze_url"

    return "unknown"


def _extract_content_id(item_id: str | None, context_data: dict[str, Any]) -> int | None:
    """Extract integer content_id from item id or context payload."""
    candidates = [item_id, context_data.get("content_id"), context_data.get("item_id")]
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            value = int(str(candidate))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _extract_model(context_data: dict[str, Any]) -> str | None:
    """Extract model name from context payload."""
    for key in ("model_spec", "model", "resolved_model"):
        value = context_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_url(context_data: dict[str, Any]) -> str | None:
    """Extract URL from context payload."""
    for key in ("url", "target_url"):
        value = context_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_instruction(context_data: dict[str, Any]) -> str:
    """Extract analyzer instruction text from context payload."""
    for key in ("instruction", "user_instruction"):
        value = context_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "None"


def _extract_summarize_context(
    session: Session,
    content: Content,
) -> tuple[str, str, int, int, list[str]]:
    """Extract text and prompt settings matching summarize handler behavior."""
    metadata = content.content_metadata or {}
    notes: list[str] = [f"content_type={content.content_type}"]
    content_type = (content.content_type or "").lower()
    prompt_type, max_bullets, max_quotes = resolve_summarization_prompt_route(
        content_type,
        url=str(content.url),
        platform=content.platform,
        metadata=metadata,
    )
    notes.append(f"prompt_type={prompt_type}")

    if content_type not in {"article", "news", "podcast"}:
        notes.append("unsupported_content_type_for_summarize")
        return "", prompt_type, max_bullets, max_quotes, notes

    source_text = get_content_body_resolver().resolve_text(session, content=content)

    if content_type == "article":
        text_payload = (
            _extract_str(source_text)
            or _extract_str(metadata.get("content"))
            or _extract_str(metadata.get("content_to_summarize"))
        )
        return text_payload, prompt_type, max_bullets, max_quotes, notes

    if content_type == "news":
        raw_content = (
            _extract_str(source_text)
            or _extract_str(metadata.get("content"))
            or _extract_str(metadata.get("content_to_summarize"))
        )
        context = _build_news_context(metadata)
        if context and raw_content:
            text_payload = f"Context:\n{context}\n\nArticle Content:\n{raw_content}"
            notes.append("aggregator_context=present")
            return text_payload, prompt_type, max_bullets, max_quotes, notes
        return raw_content, prompt_type, max_bullets, max_quotes, notes

    if content_type == "podcast":
        text_payload = (
            _extract_str(source_text)
            or _extract_str(metadata.get("transcript"))
            or _extract_str(metadata.get("content_to_summarize"))
        )
        return text_payload, prompt_type, max_bullets, max_quotes, notes

    return "", prompt_type, max_bullets, max_quotes, notes


def _default_model_for_summarize(content_type: str | None, prompt_type: str) -> str:
    """Resolve default model using the same content-type-first behavior as the summarizer."""
    normalized_content_type = (content_type or "").lower()
    if normalized_content_type in DEFAULT_SUMMARIZATION_MODELS:
        return DEFAULT_SUMMARIZATION_MODELS[normalized_content_type]
    if prompt_type.startswith("editorial_"):
        return DEFAULT_SUMMARIZATION_MODELS["editorial_narrative"]
    if prompt_type in DEFAULT_SUMMARIZATION_MODELS:
        return DEFAULT_SUMMARIZATION_MODELS[prompt_type]
    return DEFAULT_SUMMARIZATION_MODELS["article"]


def _build_news_context(metadata: dict[str, Any]) -> str:
    """Build news context string used by summarize handler for news items."""
    article = metadata.get("article", {})
    aggregator = metadata.get("aggregator", {})
    lines: list[str] = []

    article_title = _extract_str(article.get("title"))
    article_url = _extract_str(article.get("url"))
    if article_title:
        lines.append(f"Article Title: {article_title}")
    if article_url:
        lines.append(f"Article URL: {article_url}")

    if isinstance(aggregator, dict) and aggregator:
        name = _extract_str(aggregator.get("name")) or _extract_str(metadata.get("platform"))
        agg_title = _extract_str(aggregator.get("title"))
        agg_url = _extract_str(metadata.get("discussion_url")) or _extract_str(
            aggregator.get("url")
        )
        author = _extract_str(aggregator.get("author"))

        context_bits: list[str] = []
        if name:
            context_bits.append(name)
        if author:
            context_bits.append(f"by {author}")
        if agg_title and agg_title != article_title:
            lines.append(f"Aggregator Headline: {agg_title}")
        if context_bits:
            lines.append("Aggregator Context: " + ", ".join(context_bits))
        if agg_url:
            lines.append(f"Discussion URL: {agg_url}")

        extra = aggregator.get("metadata")
        if isinstance(extra, dict):
            highlights: list[str] = []
            for field in ("score", "comments_count", "likes", "retweets", "replies"):
                value = extra.get(field)
                if value is not None:
                    highlights.append(f"{field}={value}")
            if highlights:
                lines.append("Signals: " + ", ".join(highlights))

    summary_payload = metadata.get("summary")
    excerpt = _extract_str(metadata.get("excerpt"))
    if not excerpt and isinstance(summary_payload, dict):
        excerpt = (
            _extract_str(summary_payload.get("overview"))
            or _extract_str(summary_payload.get("summary"))
            or _extract_str(summary_payload.get("hook"))
            or _extract_str(summary_payload.get("takeaway"))
        )
    if excerpt:
        lines.append(f"Aggregator Summary: {excerpt}")

    return "\n".join(lines)


def _parse_iso_timestamp(value: Any) -> datetime | None:
    """Parse ISO timestamps from log payload."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _resolve_window(
    options: PromptReportOptions,
    now: datetime,
) -> tuple[datetime | None, datetime | None]:
    """Resolve effective start/end filters from options and reference time."""
    normalized_now = now if now.tzinfo else now.replace(tzinfo=UTC)

    window_start = options.since
    if window_start is not None:
        window_start = window_start if window_start.tzinfo else window_start.replace(tzinfo=UTC)
        window_start = window_start.astimezone(UTC)
    else:
        window_start = normalized_now.astimezone(UTC) - timedelta(hours=options.hours)

    window_end = options.until
    if window_end is not None:
        window_end = window_end if window_end.tzinfo else window_end.replace(tzinfo=UTC)
        window_end = window_end.astimezone(UTC)

    return window_start, window_end


def _clip_for_display(text: str | None, max_chars: int = PROMPT_PREVIEW_LIMIT) -> str:
    """Clip prompt text for markdown readability."""
    if not text:
        return "<not available>"
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2].rstrip()
    tail = text[-(max_chars // 2) :].lstrip()
    return f"{head}\n\n[... truncated ...]\n\n{tail}"


def _format_ts(value: datetime | None) -> str:
    """Format timestamps for markdown display."""
    if value is None:
        return "unknown"
    return value.isoformat()


def _extract_str(value: Any) -> str:
    """Return stripped string for scalar values, empty string otherwise."""
    if isinstance(value, str):
        return value.strip()
    return ""


def _coerce_optional_str(value: Any) -> str | None:
    """Coerce scalar to optional string."""
    if value is None:
        return None
    as_text = str(value).strip()
    return as_text or None


def _run_command(command: list[str]) -> None:
    """Run subprocess command with failure propagation."""
    subprocess.run(command, check=True)


def _remote_dir_exists(remote: str, directory: str) -> bool:
    """Check whether a remote directory exists."""
    result = subprocess.run(
        ["ssh", remote, "test", "-d", directory],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
