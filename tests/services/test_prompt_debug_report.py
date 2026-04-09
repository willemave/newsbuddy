"""Tests for local prompt debug report helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.models.schema import Content
from app.services.prompt_debug_report import (
    FailureRecord,
    PromptDebugReport,
    PromptReportOptions,
    PromptSnapshot,
    collect_log_records,
    reconstruct_analyze_url_prompt,
    reconstruct_summarize_prompt,
    render_markdown_report,
    select_failure_records,
)


def test_select_failure_records_filters_by_component_and_window(tmp_path) -> None:
    """Only matching recent error records should be selected."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    now = datetime(2026, 2, 13, 12, 0, tzinfo=UTC)
    payloads = [
        {
            "timestamp": (now - timedelta(hours=1)).isoformat(),
            "level": "ERROR",
            "component": "summarization",
            "operation": "summarize_task",
            "item_id": "10",
            "error_type": "RuntimeError",
            "error_message": "Summarization failed",
            "context_data": {"content_id": 10},
        },
        {
            "timestamp": (now - timedelta(hours=1)).isoformat(),
            "level": "INFO",
            "component": "summarization",
            "operation": "summarize_task",
            "item_id": "11",
            "message": "normal info",
            "context_data": {"content_id": 11},
        },
        {
            "timestamp": (now - timedelta(days=3)).isoformat(),
            "level": "ERROR",
            "component": "content_analyzer",
            "operation": "analyze_url",
            "error_message": "old failure",
            "context_data": {"url": "https://example.com"},
        },
        {
            "timestamp": (now - timedelta(hours=1)).isoformat(),
            "level": "ERROR",
            "component": "other_component",
            "operation": "noop",
            "error_message": "ignored failure",
            "context_data": {},
        },
    ]
    file_path = logs_dir / "test_errors.jsonl"
    with file_path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload) + "\n")

    options = PromptReportOptions(logs_dir=logs_dir, hours=24, limit=50)
    records = collect_log_records(logs_dir)
    failures = select_failure_records(records, options, now=now)

    assert len(failures) == 1
    assert failures[0].component == "summarization"
    assert failures[0].content_id == 10
    assert failures[0].phase == "summarize"


def test_reconstruct_summarize_prompt_from_content_metadata(db_session_factory) -> None:
    """Summarize prompt reconstruction should return full prompt when content exists."""
    with db_session_factory() as session:
        content = Content(
            id=42,
            content_type="article",
            url="https://example.com/article",
            title="Example",
            source="test",
            status="failed",
            content_metadata={"content": "Prompt reconstruction text."},
        )
        session.add(content)
        session.commit()

    failure = FailureRecord(
        phase="summarize",
        timestamp=datetime.now(UTC),
        component="summarization",
        operation="llm_summarization",
        source_file="errors/summarization.jsonl",
        item_id="42",
        content_id=42,
        url="https://example.com/article",
        model=None,
        error_type="RuntimeError",
        error_message="boom",
        context_data={},
    )

    snapshot = reconstruct_summarize_prompt(
        failure=failure,
        db_session_factory=db_session_factory,
    )

    assert snapshot.reconstruction_quality == "full"
    assert snapshot.phase == "summarize"
    assert snapshot.system_prompt is not None
    assert "expert editor writing an information-dense narrative summary" in snapshot.system_prompt
    assert snapshot.user_prompt is not None
    assert "Prompt reconstruction text." in snapshot.user_prompt
    assert snapshot.model == "openai:gpt-5.4-mini"


def test_reconstruct_summarize_prompt_uses_research_template_for_pdf(
    db_session_factory,
) -> None:
    with db_session_factory() as session:
        content = Content(
            id=43,
            content_type="article",
            url="https://example.com/paper.pdf",
            title="Paper",
            source="test",
            status="failed",
            content_metadata={
                "content": "Prompt reconstruction text for a paper.",
                "content_type": "pdf",
            },
        )
        session.add(content)
        session.commit()

    failure = FailureRecord(
        phase="summarize",
        timestamp=datetime.now(UTC),
        component="summarization",
        operation="llm_summarization",
        source_file="errors/summarization.jsonl",
        item_id="43",
        content_id=43,
        url="https://example.com/paper.pdf",
        model=None,
        error_type="RuntimeError",
        error_message="boom",
        context_data={},
    )

    snapshot = reconstruct_summarize_prompt(
        failure=failure,
        db_session_factory=db_session_factory,
    )

    assert snapshot.reconstruction_quality == "full"
    assert snapshot.system_prompt is not None
    assert '"template": "research"' in snapshot.system_prompt
    assert any(note == "prompt_type=editorial_research" for note in snapshot.notes)


def test_reconstruct_analyze_url_prompt_partial() -> None:
    """Analyze prompt reconstruction should return a partial skeleton when URL is present."""
    failure = FailureRecord(
        phase="analyze_url",
        timestamp=datetime.now(UTC),
        component="content_analyzer",
        operation="analyze_url",
        source_file="errors/content_analyzer.jsonl",
        item_id=None,
        content_id=None,
        url="https://example.com/topic",
        model=None,
        error_type="APIError",
        error_message="request failed",
        context_data={"url": "https://example.com/topic"},
    )
    snapshot = reconstruct_analyze_url_prompt(failure)

    assert snapshot.reconstruction_quality == "partial"
    assert snapshot.phase == "analyze_url"
    assert snapshot.system_prompt is not None
    assert "You classify web pages as article, podcast, or video" in snapshot.system_prompt
    assert snapshot.user_prompt is not None
    assert "URL: https://example.com/topic" in snapshot.user_prompt
    assert snapshot.model == "gpt-5.4"


def test_render_markdown_report_contains_failure_sections() -> None:
    """Markdown renderer should include summary and failure prompt sections."""
    snapshot = PromptSnapshot(
        phase="summarize",
        reconstruction_quality="full",
        timestamp=datetime.now(UTC),
        source_file="errors/sample.jsonl",
        component="summarization",
        operation="llm_summarization",
        content_id=7,
        url="https://example.com",
        model="openai:gpt-5.4-mini",
        error_type="RuntimeError",
        error_message="example failure",
        system_prompt="system prompt text",
        user_prompt="user prompt text",
        notes=["note one"],
    )
    report = PromptDebugReport(
        generated_at=datetime.now(UTC),
        logs_dir="./logs_from_server",
        db_url="<from app settings>",
        window_hours=24,
        total_records_scanned=10,
        total_failures=1,
        by_phase={"summarize": 1},
        by_component={"summarization": 1},
        by_model={"openai:gpt-5.4-mini": 1},
        snapshots=[snapshot],
    )

    markdown = render_markdown_report(report)
    assert "# Prompt Debug Report" in markdown
    assert "## Counts By Phase" in markdown
    assert "### 1. summarize (full)" in markdown
    assert "#### System Prompt" in markdown
    assert "#### User Prompt" in markdown


def test_select_failure_records_supports_explicit_since_until(tmp_path) -> None:
    """Explicit since/until filters should constrain included failures."""
    logs_dir = tmp_path / "logs_window"
    logs_dir.mkdir(parents=True, exist_ok=True)

    now = datetime(2026, 2, 13, 12, 0, tzinfo=UTC)
    payloads = [
        {
            "timestamp": (now - timedelta(days=1)).isoformat(),
            "level": "ERROR",
            "component": "summarization",
            "operation": "summarize_task",
            "item_id": "101",
            "error_message": "old failure",
            "context_data": {"content_id": 101},
        },
        {
            "timestamp": (now - timedelta(hours=2)).isoformat(),
            "level": "ERROR",
            "component": "summarization",
            "operation": "summarize_task",
            "item_id": "102",
            "error_message": "recent failure",
            "context_data": {"content_id": 102},
        },
    ]
    file_path = logs_dir / "window_errors.jsonl"
    with file_path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload) + "\n")

    options = PromptReportOptions(
        logs_dir=logs_dir,
        hours=24,
        since=now - timedelta(hours=4),
        until=now - timedelta(minutes=30),
    )
    records = collect_log_records(logs_dir)
    failures = select_failure_records(records, options, now=now)

    assert len(failures) == 1
    assert failures[0].content_id == 102
