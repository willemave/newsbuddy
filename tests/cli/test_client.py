"""Tests for the Newsly agent HTTP client."""

from __future__ import annotations

import json

import requests

from cli.newsly_agent.client import NewslyAgentClient, WaitOptions


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode("utf-8")
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_client_sends_bearer_api_key(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(status_code=200, payload={"ok": True})

    session = requests.Session()
    monkeypatch.setattr(session, "request", fake_request)
    client = NewslyAgentClient(
        server_url="https://example.com",
        api_key="newsly_ak_test",
        session=session,
    )

    response = client.request("GET", "/api/jobs/1")

    assert response == {"ok": True}
    assert captured["url"] == "https://example.com/api/jobs/1"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer newsly_ak_test"


def test_wait_for_job_polls_until_terminal(monkeypatch) -> None:
    payloads = iter(
        [
            {"id": 12, "status": "pending"},
            {"id": 12, "status": "processing"},
            {"id": 12, "status": "completed"},
        ]
    )

    session = requests.Session()
    monkeypatch.setattr(
        session,
        "request",
        lambda **_kwargs: _FakeResponse(status_code=200, payload=next(payloads)),
    )
    client = NewslyAgentClient(
        server_url="https://example.com",
        api_key="newsly_ak_test",
        session=session,
    )

    job = client.wait_for_job(12, WaitOptions(interval_seconds=0.0, timeout_seconds=1.0))

    assert job["status"] == "completed"
