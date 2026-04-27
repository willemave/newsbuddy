"""Tests for admin config and output helpers."""

from __future__ import annotations

import argparse
from io import StringIO

import pytest

from admin.cli import build_parser
from admin.config import resolve_config
from admin.output import Envelope, EnvelopeError, emit


def _namespace(**overrides: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "env_file": None,
        "remote": None,
        "app_dir": None,
        "docker_service_name": None,
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
                "ADMIN_DOCKER_SERVICE_NAME=newsly",
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
    assert config.docker_service_name == "newsly"
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
    assert "admin logs tail --limit 200" in rendered


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
                        "docker compose exec -T newsly python -m admin.remote logs.list: "
                        "permission denied"
                    )
                },
            ),
        ),
        "text",
        stream,
    )

    rendered = stream.getvalue()
    assert "Docker could not run the newsly container command" in rendered
    assert "docker ps" in rendered


def test_build_parser_defaults_to_text_output():
    args = build_parser().parse_args(["health", "snapshot"])

    assert args.output == "text"


def test_build_parser_supports_health_queue():
    args = build_parser().parse_args(["health", "queue", "--window-hours", "6"])

    assert args.health_command == "queue"
    assert args.window_hours == 6


def test_build_parser_supports_health_config():
    args = build_parser().parse_args(["health", "config"])

    assert args.health_command == "config"


def test_build_parser_supports_logs_exceptions():
    args = build_parser().parse_args(["logs", "exceptions", "--limit", "7"])

    assert args.logs_command == "exceptions"
    assert args.limit == 7


def test_build_parser_supports_usage_group_by_vendor():
    args = build_parser().parse_args(["usage", "summary", "--group-by", "vendor"])

    assert args.usage_command == "summary"
    assert args.group_by == "vendor"


def test_build_parser_supports_docker_log_source():
    args = build_parser().parse_args(["logs", "tail", "--source", "docker", "--limit", "12"])

    assert args.logs_command == "tail"
    assert args.source == "docker"
    assert args.limit == 12


def test_build_parser_defaults_logs_tail_to_docker():
    args = build_parser().parse_args(["logs", "tail"])

    assert args.logs_command == "tail"
    assert args.source == "docker"
    assert args.limit == 50


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


def test_emit_text_usage_summary_includes_vendor_units():
    stream = StringIO()
    emit(
        Envelope(
            ok=True,
            command="usage.summary",
            data={
                "group_by": "vendor",
                "totals": {
                    "call_count": 2,
                    "total_tokens": 100,
                    "request_count": 2,
                    "resource_count": 9,
                    "cost_usd": 0.42,
                },
                "groups": [
                    {
                        "key": "exa",
                        "call_count": 1,
                        "total_tokens": 0,
                        "request_count": 1,
                        "resource_count": 8,
                        "cost_usd": 0.28,
                    },
                    {
                        "key": "openai",
                        "call_count": 1,
                        "total_tokens": 100,
                        "request_count": 0,
                        "resource_count": 0,
                        "cost_usd": 0.14,
                    },
                ],
            },
        ),
        "text",
        stream,
    )

    rendered = stream.getvalue()
    assert "Totals: 2 calls, 100 tokens, 2 requests, 9 resources, $0.4200" in rendered
    assert "- exa: 1 calls, 1 requests, 8 resources, $0.2800" in rendered
    assert "- openai: 1 calls, 100 tokens, $0.1400" in rendered


def test_emit_text_health_config_summarizes_redacted_groups():
    stream = StringIO()
    emit(
        Envelope(
            ok=True,
            command="health.config",
            data={
                "environment": "production",
                "redacted": True,
                "groups": {
                    "auth": {
                        "jwt_secret_configured": True,
                        "admin_password_configured": True,
                    },
                    "queue": {
                        "max_workers": 1,
                        "worker_timeout_seconds": 300,
                    },
                    "providers": {
                        "openai_api_key_configured": True,
                        "exa_api_key_configured": False,
                    },
                },
            },
        ),
        "text",
        stream,
    )

    rendered = stream.getvalue()
    assert "Config diagnostics:" in rendered
    assert "- environment: production" in rendered
    assert "- auth: 2/2 configured" in rendered
    assert "- providers: 1/2 configured" in rendered
    assert "- queue: 2 settings" in rendered


def test_logs_group_error_includes_next_steps(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["logs"])

    captured = capsys.readouterr()
    assert "Pick a logs subcommand:" in captured.err
    assert "admin logs list" in captured.err


def test_emit_text_docker_logs_are_rendered_raw():
    stream = StringIO()
    emit(
        Envelope(
            ok=True,
            command="logs.tail",
            data={
                "source": "docker",
                "stdout": "newsly  | 2026-04-09T12:00:00Z booted\nnewsly  | ready\n",
            },
        ),
        "text",
        stream,
    )

    rendered = stream.getvalue()
    assert "newsly  | ready" in rendered


def test_emit_text_docker_logs_fall_back_to_stderr():
    stream = StringIO()
    emit(
        Envelope(
            ok=True,
            command="logs.tail",
            data={
                "source": "docker",
                "stdout": "",
                "stderr": "newsly  | 2026-04-09T12:00:00Z worker warning\n",
            },
        ),
        "text",
        stream,
    )

    rendered = stream.getvalue()
    assert "worker warning" in rendered
