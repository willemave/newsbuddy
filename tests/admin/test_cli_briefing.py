"""Tests for local briefing admin command behavior."""

from __future__ import annotations

from pathlib import Path

from admin.cli import _handle_briefing, build_parser
from admin.config import AdminConfig


def _config(tmp_path: Path) -> AdminConfig:
    return AdminConfig(
        env_file=tmp_path / ".env",
        remote="willem@host",
        app_dir="/opt/news_app",
        docker_service_name="newsly",
        logs_dir="/data/logs",
        service_log_dir="/var/log/news_app",
        remote_db_path="/data/news_app.db",
        remote_python=".venv/bin/python",
        remote_context_source="direct",
        local_logs_dir=tmp_path / "logs",
        local_db_path=tmp_path / "news_app.db",
        prompt_report_output_dir=tmp_path / "outputs",
    )


def test_briefing_refresh_payload_uses_production_llm_path(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_invoke_remote(action, *, config, payload):  # noqa: ANN001
        captured["action"] = action
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr("admin.cli._invoke_remote", fake_invoke_remote)
    args = build_parser().parse_args(["briefing", "refresh", "--user-id", "1"])

    result = _handle_briefing(args, config=_config(tmp_path))

    assert result.data == {"ok": True}
    assert captured == {
        "action": "briefing.refresh",
        "payload": {"unsafe_raw": False, "user_id": 1, "mode": "append"},
    }
