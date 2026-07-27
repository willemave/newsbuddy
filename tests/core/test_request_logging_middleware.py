"""Tests for request logging middleware behavior."""

import logging

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import MAX_LOGGABLE_REQUEST_BODY_BYTES, app


def test_request_logging_propagates_request_id(caplog) -> None:
    """Middleware should echo inbound request IDs and emit structured metadata."""
    with TestClient(app) as client, caplog.at_level(logging.INFO):
        response = client.get("/", headers={"X-Request-ID": "req-test-123"})

    assert response.headers["X-Request-ID"] == "req-test-123"
    matching = [
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "http.request"
        and getattr(record, "status", None) == "completed"
    ]
    assert matching
    assert getattr(matching[-1], "request_id", None) == "req-test-123"


def _started_request_record(caplog):
    matching = [
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "http.request"
        and getattr(record, "status", None) == "started"
        and getattr(record, "http_details", {}).get("path") == "/"
    ]
    assert matching
    return matching[-1]


def test_request_logging_summarizes_small_json_body(monkeypatch, caplog) -> None:
    original_body = Request.body
    body_calls = 0

    async def recording_body(request):
        nonlocal body_calls
        body_calls += 1
        return await original_body(request)

    monkeypatch.setattr(Request, "body", recording_body)

    with TestClient(app) as client, caplog.at_level(logging.INFO):
        response = client.post("/", json={"prompt": "hello", "count": 2})

    assert response.status_code == 405
    assert body_calls == 1
    payload_summary = _started_request_record(caplog).http_details["payload_summary"]
    assert payload_summary["shape"] == "json_object"
    assert payload_summary["top_level_keys"] == ["count", "prompt"]


def test_request_logging_summarizes_small_form_body(monkeypatch, caplog) -> None:
    original_body = Request.body
    body_calls = 0

    async def recording_body(request):
        nonlocal body_calls
        body_calls += 1
        return await original_body(request)

    monkeypatch.setattr(Request, "body", recording_body)

    with TestClient(app) as client, caplog.at_level(logging.INFO):
        response = client.post("/", data={"email": "test@example.com", "password": "secret"})

    assert response.status_code == 405
    assert body_calls == 1
    payload_summary = _started_request_record(caplog).http_details["payload_summary"]
    assert payload_summary["shape"] == "form"
    assert payload_summary["field_names"] == ["email", "password"]


@pytest.mark.parametrize(
    ("request_kwargs", "expected_reason"),
    [
        (
            {"files": {"file": ("audio.m4a", b"audio-bytes", "audio/mp4")}},
            "multipart",
        ),
        (
            {
                "content": b"binary-data",
                "headers": {"content-type": "application/octet-stream"},
            },
            "binary",
        ),
        (
            {
                "content": b'{"payload":"' + b"x" * MAX_LOGGABLE_REQUEST_BODY_BYTES + b'"}',
                "headers": {"content-type": "application/json"},
            },
            "size_limit",
        ),
    ],
)
def test_request_logging_does_not_buffer_upload_binary_or_large_bodies(
    monkeypatch,
    caplog,
    request_kwargs,
    expected_reason,
) -> None:
    body_calls = 0

    async def recording_body(_request):
        nonlocal body_calls
        body_calls += 1
        raise AssertionError("request body must not be buffered")

    monkeypatch.setattr(Request, "body", recording_body)

    with TestClient(app) as client, caplog.at_level(logging.INFO):
        response = client.post("/", **request_kwargs)

    assert response.status_code == 405
    assert body_calls == 0
    payload_summary = _started_request_record(caplog).http_details["payload_summary"]
    assert payload_summary["shape"] == "not_inspected"
    assert payload_summary["reason"] == expected_reason
