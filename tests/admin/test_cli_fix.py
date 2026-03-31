"""Tests for local fix command behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from admin.cli import AdminCLIError, _handle_fix, build_parser
from admin.config import AdminConfig


def _config(tmp_path: Path) -> AdminConfig:
    return AdminConfig(
        env_file=tmp_path / ".env",
        remote="willem@host",
        app_dir="/opt/news_app",
        logs_dir="/data/logs",
        service_log_dir="/var/log/news_app",
        remote_db_path="/data/news_app.db",
        remote_python=".venv/bin/python",
        remote_context_source="direct",
        local_logs_dir=tmp_path / "logs",
        local_db_path=tmp_path / "news_app.db",
        prompt_report_output_dir=tmp_path / "outputs",
    )


def test_fix_requeue_stale_preview_uses_dry_run(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    args = build_parser().parse_args(["fix", "requeue-stale", "--hours", "3"])

    def fake_run_remote_script(config, script_args):
        captured["config"] = config
        captured["script_args"] = script_args
        return {"stdout": "preview"}

    monkeypatch.setattr("admin.cli.run_remote_script", fake_run_remote_script)

    result = _handle_fix(args, config=_config(tmp_path))

    assert result.data["stdout"] == "preview"
    assert captured["script_args"] == [
        "scripts/queue_control.py",
        "requeue-stale",
        "--hours",
        "3.0",
        "--dry-run",
    ]


def test_fix_requeue_stale_apply_requires_yes(tmp_path):
    args = build_parser().parse_args(["fix", "--apply", "requeue-stale"])

    with pytest.raises(AdminCLIError, match="requires both --apply and --yes"):
        _handle_fix(args, config=_config(tmp_path))


def test_fix_reset_content_preview_uses_remote_helper(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    args = build_parser().parse_args(["fix", "reset-content", "--hours", "4"])

    def fake_invoke_remote(action, *, config, payload):
        captured["action"] = action
        captured["payload"] = payload
        return {"deleted_tasks": 3}

    monkeypatch.setattr("admin.cli._invoke_remote", fake_invoke_remote)

    result = _handle_fix(args, config=_config(tmp_path))

    assert result.data == {"deleted_tasks": 3}
    assert captured["action"] == "fix.preview-reset-content"
    assert captured["payload"] == {"cancel_only": False, "hours": 4.0, "content_type": None}


def test_fix_run_scraper_preview_does_not_execute(monkeypatch, tmp_path):
    args = build_parser().parse_args(["fix", "run-scraper", "--scraper", "HackerNews"])

    monkeypatch.setattr(
        "admin.cli.run_remote_script",
        lambda *args, **kwargs: pytest.fail("run_remote_script should not be called"),
    )

    result = _handle_fix(args, config=_config(tmp_path))

    assert result.data["preview"] is True
    assert result.data["command"] == [
        "scripts/run_scrapers.py",
        "--scrapers",
        "HackerNews",
    ]
