"""Tests for admin config and output helpers."""

from __future__ import annotations

import argparse
from io import StringIO

import pytest

from admin.cli import build_parser
from admin.config import resolve_config
from admin.output import Envelope, EnvelopeError, emit


def _namespace(**overrides: object) -> argparse.Namespace:
    defaults = {
        "env_file": None,
        "remote": None,
        "app_dir": None,
        "logs_dir": None,
        "service_log_dir": None,
        "remote_db_path": None,
        "remote_python": None,
        "remote_context_source": None,
        "local_logs_dir": None,
        "local_db_path": None,
        "prompt_report_output_dir": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_resolve_config_loads_admin_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ADMIN_REMOTE=ops@example.com",
                "ADMIN_APP_DIR=/srv/news_app",
                "ADMIN_LOGS_DIR=/srv/logs",
                "ADMIN_SERVICE_LOG_DIR=/srv/service-logs",
                "ADMIN_REMOTE_DB_PATH=/srv/news.db",
                "ADMIN_REMOTE_PYTHON=/venv/bin/python",
                "ADMIN_REMOTE_CONTEXT_SOURCE=app-settings",
            ]
        )
    )

    config = resolve_config(_namespace(env_file=str(env_file)))

    assert config.remote == "ops@example.com"
    assert config.app_dir == "/srv/news_app"
    assert config.logs_dir == "/srv/logs"
    assert config.service_log_dir == "/srv/service-logs"
    assert config.remote_db_path == "/srv/news.db"
    assert config.remote_python == "/venv/bin/python"
    assert config.remote_context_source == "app-settings"


def test_resolve_config_prefers_flags_over_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_REMOTE=ops@example.com\n")

    config = resolve_config(_namespace(env_file=str(env_file), remote="override@example.com"))

    assert config.remote == "override@example.com"


def test_emit_json_envelope():
    stream = StringIO()
    emit(Envelope(ok=True, command="db.tables", data={"tables": ["users"]}), "json", stream)

    rendered = stream.getvalue()
    assert '"command": "db.tables"' in rendered
    assert '"tables": [' in rendered


def test_emit_text_error_envelope():
    stream = StringIO()
    emit(
        Envelope(
            ok=False,
            command="db.query",
            error=EnvelopeError("bad query", details={"sql": "delete from users"}),
        ),
        "text",
        stream,
    )

    rendered = stream.getvalue()
    assert "error: bad query" in rendered
    assert '"sql": "delete from users"' in rendered


def test_emit_text_logs_list_envelope_is_human_readable():
    stream = StringIO()
    emit(
        Envelope(
            ok=True,
            command="logs.list",
            data={
                "sources": {
                    "structured": [{"path": "/tmp/structured.jsonl"}],
                    "worker": [{"path": "/tmp/worker.log"}],
                }
            },
        ),
        "text",
        stream,
    )

    rendered = stream.getvalue()
    assert "Available log sources:" in rendered
    assert "- structured (1 file)" in rendered
    assert "- worker (1 file)" in rendered
    assert "admin logs tail --source structured --limit 20" in rendered


def test_emit_text_permission_error_envelope_is_actionable():
    stream = StringIO()
    emit(
        Envelope(
            ok=False,
            command="logs.list",
            error=EnvelopeError(
                "Remote command failed for action 'logs.list'",
                details={
                    "stderr": (
                        "PermissionError: [Errno 13] Permission denied: "
                        "'/opt/news_app/.env'"
                    )
                },
            ),
        ),
        "text",
        stream,
    )

    rendered = stream.getvalue()
    assert "could not read `/opt/news_app/.env`" in rendered
    assert "ADMIN_REMOTE_CONTEXT_SOURCE=direct" in rendered


def test_build_parser_defaults_to_text_output():
    args = build_parser().parse_args(["health", "snapshot"])

    assert args.output == "text"


def test_build_parser_supports_logs_exceptions():
    args = build_parser().parse_args(["logs", "exceptions", "--limit", "7"])

    assert args.logs_command == "exceptions"
    assert args.limit == 7


def test_emit_text_logs_exceptions_is_human_readable():
    stream = StringIO()
    emit(
        Envelope(
            ok=True,
            command="logs.exceptions",
            data={
                "exceptions": [
                    {
                        "timestamp": "2026-03-30T12:00:00Z",
                        "component": "worker",
                        "operation": "summarize",
                        "error_type": "ValueError",
                        "error_message": "boom",
                    }
                ]
            },
        ),
        "text",
        stream,
    )

    rendered = stream.getvalue()
    assert "recent exception record" in rendered
    assert "worker/summarize ValueError: boom" in rendered


def test_logs_group_error_includes_next_steps(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["logs"])

    captured = capsys.readouterr()
    assert "Pick a logs subcommand:" in captured.err
    assert "admin logs list" in captured.err


def test_logs_tail_error_includes_source_examples(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["logs", "tail"])

    captured = capsys.readouterr()
    assert "Choose one log source with `--source`." in captured.err
    assert "admin logs list" in captured.err
