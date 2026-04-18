"""Tests for SSH helpers used by the admin CLI."""

from __future__ import annotations

import subprocess
from typing import Any, cast

from admin.config import AdminConfig
from admin.ssh import run_remote_docker_logs, run_remote_module, run_remote_script


def _config() -> AdminConfig:
    return AdminConfig(
        env_file=None,  # type: ignore[arg-type]
        remote="willem@host",
        app_dir="/opt/news_app",
        docker_service_name="newsly",
        logs_dir="/data/logs",
        service_log_dir="/var/log/news_app",
        remote_db_path="postgresql://newsly:secret@127.0.0.1:5432/news_app",
        remote_python=".venv/bin/python",
        remote_context_source="app-settings",
        local_logs_dir=None,  # type: ignore[arg-type]
        local_db_path=None,  # type: ignore[arg-type]
        prompt_report_output_dir=None,  # type: ignore[arg-type]
    )


def test_run_remote_module_builds_expected_ssh_command(monkeypatch) -> None:
    captured: dict[str, object] = {"calls": []}

    def fake_run(*args, **kwargs):
        command = args[0]
        captured["calls"].append(command)
        if command[2].endswith("sudo docker exec newsly env"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "DATABASE_URL=postgresql+psycopg://newsly:change-me@127.0.0.1:5432/newsly\n"
                    "POSTGRES_PASSWORD=secret\n"
                    "POSTGRES_USER=newsly\n"
                    "POSTGRES_PORT=5432\n"
                    "POSTGRES_DB=newsly\n"
                ),
                stderr="",
            )
        captured["args"] = command
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"ok": true, "data": {"x": 1}}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_remote_module(_config(), action="db.tables", payload={"limit": 10})

    assert result == {"ok": True, "data": {"x": 1}}
    assert cast(list[list[str]], captured["calls"])[0] == [
        "ssh",
        "willem@host",
        "cd /opt/news_app && sudo docker exec newsly env",
    ]
    assert cast(list[str], captured["args"]) == [
        "ssh",
        "willem@host",
        "cd /opt/news_app && sudo docker exec -i newsly python -m admin.remote db.tables",
    ]
    assert cast(str, captured["input"]) == (
        '{"payload": {"limit": 10}, "context_override": {"database_url": '
        '"postgresql+psycopg://newsly:secret@127.0.0.1:5432/newsly", "logs_dir": "/data/logs", '
        '"service_log_dir": "/var/log/news_app"}}'
    )


def test_run_remote_docker_logs_builds_expected_ssh_command(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args[0]
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="newsly  | booted\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_remote_docker_logs(_config(), tail=25)

    assert result["stdout"] == "newsly  | booted\n"
    assert result["source"] == "docker"
    assert cast(list[str], captured["args"]) == [
        "ssh",
        "willem@host",
        "cd /opt/news_app && sudo docker logs --timestamps --tail 25 newsly",
    ]


def test_run_remote_script_injects_runtime_database_url(monkeypatch) -> None:
    captured: dict[str, object] = {"calls": []}

    def fake_run(*args, **kwargs):
        command = args[0]
        cast(list[list[str]], captured["calls"]).append(command)
        if command[2].endswith("sudo docker exec newsly env"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "DATABASE_URL=postgresql+psycopg://newsly:change-me@127.0.0.1:5432/newsly\n"
                    "POSTGRES_PASSWORD=secret\n"
                    "POSTGRES_USER=newsly\n"
                    "POSTGRES_PORT=5432\n"
                    "POSTGRES_DB=newsly\n"
                ),
                stderr="",
            )
        captured["args"] = command
        return subprocess.CompletedProcess(command, 0, stdout="done\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_remote_script(_config(), ["scripts/example.py"])

    assert result["stdout"] == "done\n"
    assert cast(list[str], captured["args"]) == [
        "ssh",
        "willem@host",
        (
            "cd /opt/news_app && sudo docker exec -i -e NEWSLY_ENV_FILE=/tmp/empty.env "
            "-e DATABASE_URL=postgresql+psycopg://newsly:secret@127.0.0.1:5432/newsly "
            "newsly python scripts/example.py"
        ),
    ]
