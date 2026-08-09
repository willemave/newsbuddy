"""Tests for admin logs helpers."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.admin_web import logs as logs_router


def test_get_recent_structured_events_empty_dir(tmp_path, monkeypatch) -> None:
    """Structured helper returns empty list when directory is missing."""

    missing_dir = tmp_path / "structured"
    monkeypatch.setattr(logs_router, "STRUCTURED_DIR", missing_dir)

    events = logs_router._get_recent_structured_events(limit=5)

    assert events == []


def test_get_recent_structured_events_reads_latest_entries(tmp_path, monkeypatch) -> None:
    """Structured helper returns newest events first with level metadata."""

    structured_dir = tmp_path / "structured"
    structured_dir.mkdir(parents=True, exist_ok=True)
    log_file = structured_dir / "voice_trace.jsonl"
    entries = [
        {
            "timestamp": "2026-02-15T16:00:00+00:00",
            "level": "INFO",
            "component": "voice_ws",
            "operation": "connect",
            "message": "Voice websocket connected",
        },
        {
            "timestamp": "2026-02-15T16:00:10+00:00",
            "level": "INFO",
            "component": "voice_orchestrator",
            "operation": "turn_start",
            "message": "Voice turn started",
        },
        {
            "timestamp": "2026-02-15T16:00:20+00:00",
            "level": "INFO",
            "component": "voice_orchestrator",
            "operation": "turn_complete",
            "message": "Voice turn completed",
        },
    ]
    with open(log_file, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    monkeypatch.setattr(logs_router, "STRUCTURED_DIR", structured_dir)
    events = logs_router._get_recent_structured_events(limit=2)

    assert len(events) == 2
    assert events[0]["operation"] == "turn_complete"
    assert events[1]["operation"] == "turn_start"
    assert events[0]["level"] == "INFO"


def test_matches_structured_filters_handles_missing_values() -> None:
    """Filter matcher should reject entries missing a filtered key."""
    entry = {"component": "http", "operation": "request"}

    assert logs_router._matches_structured_filters(entry, {"request_id": "req_a"}) is False
    assert logs_router._matches_structured_filters(entry, {"component": "http"}) is True


def test_resolve_log_file_rejects_prefix_sibling_and_escaping_symlink(
    tmp_path, monkeypatch
) -> None:
    logs_dir = tmp_path / "logs"
    sibling_dir = tmp_path / "logs-private"
    logs_dir.mkdir()
    sibling_dir.mkdir()
    secret = sibling_dir / "secret.log"
    secret.write_text("private", encoding="utf-8")
    (logs_dir / "escape.log").symlink_to(secret)
    monkeypatch.setattr(logs_router, "LOGS_DIR", logs_dir)

    for filename in ("../logs-private/secret.log", "escape.log"):
        with pytest.raises(HTTPException) as exc_info:
            logs_router._resolve_log_file(filename)
        assert exc_info.value.status_code == 404


def test_log_download_route_precedes_greedy_view_route(
    client_factory, test_user, tmp_path, monkeypatch
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "app.log").write_text("download me", encoding="utf-8")
    monkeypatch.setattr(logs_router, "LOGS_DIR", logs_dir)

    with client_factory(
        user=test_user,
        extra_overrides={logs_router.require_admin: lambda: test_user},
    ) as client:
        response = client.get("/admin/logs/app.log/download")

    assert response.status_code == 200
    assert response.text == "download me"
    assert response.headers["content-disposition"] == 'attachment; filename="app.log"'
