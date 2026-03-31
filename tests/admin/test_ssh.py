"""Tests for SSH helpers used by the admin CLI."""

from __future__ import annotations

import subprocess

from admin.config import AdminConfig
from admin.ssh import run_remote_module


def _config() -> AdminConfig:
    return AdminConfig(
        env_file=None,  # type: ignore[arg-type]
        remote="willem@host",
        app_dir="/opt/news_app",
        logs_dir="/data/logs",
        service_log_dir="/var/log/news_app",
        remote_db_path="/data/news_app.db",
        remote_python=".venv/bin/python",
        remote_context_source="direct",
        local_logs_dir=None,  # type: ignore[arg-type]
        local_db_path=None,  # type: ignore[arg-type]
        prompt_report_output_dir=None,  # type: ignore[arg-type]
    )


def test_run_remote_module_builds_expected_ssh_command(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args[0]
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout='{"ok": true, "data": {"x": 1}}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_remote_module(_config(), action="db.tables", payload={"limit": 10})

    assert result == {"ok": True, "data": {"x": 1}}
    assert captured["args"] == [
        "ssh",
        "willem@host",
        "cd /opt/news_app && .venv/bin/python -m admin.remote db.tables",
    ]
    assert captured["input"] == (
        '{"payload": {"limit": 10}, "context_override": {"database_url": '
        '"sqlite:////data/news_app.db", "logs_dir": "/data/logs", '
        '"service_log_dir": "/var/log/news_app"}}'
    )
