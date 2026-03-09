"""Smoke tests for the Newsly agent CLI entrypoint."""

from __future__ import annotations

import json

from cli.newsly_agent.config import AgentCliConfig, save_config
from cli.newsly_agent.main import main


def test_jobs_get_outputs_json_envelope(tmp_path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "config.json"
    save_config(
        AgentCliConfig(server_url="https://example.com", api_key="newsly_ak_test"),
        str(config_path),
    )

    class _FakeClient:
        def request(self, method, path, params=None, json_body=None):
            assert method == "GET"
            assert path == "/api/jobs/77"
            assert params is None
            assert json_body is None
            return {"id": 77, "status": "completed"}

        def wait_for_job(self, job_id, options):
            raise AssertionError(f"unexpected wait for {job_id} {options}")

    monkeypatch.setattr("cli.newsly_agent.main.build_client", lambda args: _FakeClient())

    exit_code = main(["--config", str(config_path), "jobs", "get", "77"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["command"] == "jobs.get"
    assert captured["data"]["id"] == 77
    assert captured["ok"] is True


def test_content_submit_wait_adds_job_payload(tmp_path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "config.json"
    save_config(
        AgentCliConfig(server_url="https://example.com", api_key="newsly_ak_test"),
        str(config_path),
    )

    class _FakeClient:
        def request(self, method, path, params=None, json_body=None):
            assert method == "POST"
            assert path == "/api/content/submit"
            assert params is None
            assert json_body == {"url": "https://example.com/story"}
            return {"content_id": 9, "task_id": 314}

        def wait_for_job(self, job_id, options):
            assert job_id == 314
            assert options.interval_seconds == 0.0
            assert options.timeout_seconds == 5.0
            return {"id": 314, "status": "completed"}

    monkeypatch.setattr("cli.newsly_agent.main.build_client", lambda args: _FakeClient())

    exit_code = main(
        [
            "--config",
            str(config_path),
            "content",
            "submit",
            "--url",
            "https://example.com/story",
            "--wait",
            "--wait-interval",
            "0",
            "--wait-timeout",
            "5",
        ]
    )

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["data"]["task_id"] == 314
    assert captured["job"]["status"] == "completed"


def test_config_commands_write_expected_values(tmp_path, capsys) -> None:
    config_path = tmp_path / "config.json"

    first_exit = main(
        [
            "--config",
            str(config_path),
            "config",
            "set-server",
            "https://example.com",
        ]
    )
    first_output = json.loads(capsys.readouterr().out)
    second_exit = main(
        [
            "--config",
            str(config_path),
            "config",
            "set-api-key",
            "newsly_ak_test",
        ]
    )
    second_output = json.loads(capsys.readouterr().out)

    assert first_exit == 0
    assert second_exit == 0
    assert first_output["data"]["server_url"] == "https://example.com"
    assert second_output["data"]["api_key_set"] is True


def test_digest_list_uses_mobile_facing_route(tmp_path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "config.json"
    save_config(
        AgentCliConfig(server_url="https://example.com", api_key="newsly_ak_test"),
        str(config_path),
    )

    class _FakeClient:
        def request(self, method, path, params=None, json_body=None):
            assert method == "GET"
            assert path == "/api/content/daily-digests"
            assert params == {"limit": 20, "read_filter": "unread"}
            assert json_body is None
            return {"digests": [], "meta": {"has_more": False, "next_cursor": None}}

        def wait_for_job(self, job_id, options):
            raise AssertionError(f"unexpected wait for {job_id} {options}")

    monkeypatch.setattr("cli.newsly_agent.main.build_client", lambda args: _FakeClient())

    exit_code = main(["--config", str(config_path), "digest", "list"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["command"] == "digest.list"
    assert captured["ok"] is True
