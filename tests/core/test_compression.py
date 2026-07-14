import logging

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.core.compression import PathScopedGZipMiddleware


def test_briefing_compression_logs_serialized_and_wire_bytes(caplog) -> None:
    app = FastAPI()
    app.add_middleware(
        PathScopedGZipMiddleware,
        path_prefixes=("/api/briefing",),
        minimum_size=100,
    )

    @app.get("/api/briefing/test")
    def briefing_payload() -> PlainTextResponse:
        return PlainTextResponse("a" * 4_096)

    with TestClient(app) as client, caplog.at_level(logging.INFO):
        response = client.get(
            "/api/briefing/test",
            headers={"Accept-Encoding": "gzip"},
        )

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    records = [
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "briefing.response.compression"
    ]
    assert len(records) == 1
    sizes = records[0].context_data
    assert sizes["uncompressed_bytes"] == 4_096
    assert 0 < sizes["compressed_bytes"] < sizes["uncompressed_bytes"]
