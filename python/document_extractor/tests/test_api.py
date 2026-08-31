from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from newsly_document_extractor.api import create_app
from newsly_document_extractor.models import (
    SCHEMA_VERSION,
    ExtractionFailure,
    ExtractionFailureCode,
    ExtractOptions,
    ExtractRequest,
)
from newsly_document_extractor.settings import ExtractorSettings


class FakePolicy:
    def __init__(self) -> None:
        self.closed = False

    async def extract(self, request: ExtractRequest) -> ExtractionFailure:
        return ExtractionFailure(
            schema_version=SCHEMA_VERSION,
            request_id=request.request_id,
            kind="failure",
            code=ExtractionFailureCode.NO_CONTENT,
            retryable=False,
            http_status=None,
            message="No readable content was extracted",
            timings=[],
        )

    async def close(self) -> None:
        self.closed = True


def _request_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": "api-auth-test",
        "url": "https://example.com/article",
        "intent": "extract_article",
        "absolute_deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        "options": ExtractOptions.defaults().model_dump(mode="json"),
        "trace": {"trace_id": None, "span_id": None},
    }


def test_extract_fails_closed_without_a_configured_secret() -> None:
    app = create_app(ExtractorSettings(environment="test", shared_secret=None))

    with TestClient(app) as client:
        readiness = client.get("/health/ready")
        response = client.post("/v1/extract", json=_request_payload())

    assert readiness.status_code == 503
    assert response.status_code == 503


@pytest.mark.parametrize("variable", ["DATABASE_URL", "NEWSLY_DATABASE_URL"])
@pytest.mark.parametrize("environment", ["development", "test", "production"])
def test_every_environment_rejects_database_configuration(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    environment: Literal["development", "test", "production"],
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("NEWSLY_DATABASE_URL", raising=False)
    monkeypatch.setenv(variable, "postgresql://must-not-enter-extractor")

    with pytest.raises(ValidationError, match="Database configuration must not be present"):
        ExtractorSettings(environment=environment, shared_secret="test-secret")


def test_production_requires_the_private_service_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("NEWSLY_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="SHARED_SECRET is required"):
        ExtractorSettings(environment="production", shared_secret=None)


def test_extract_rejects_a_missing_token() -> None:
    app = create_app(ExtractorSettings(environment="test", shared_secret="test-secret"))

    with TestClient(app) as client:
        response = client.post("/v1/extract", json=_request_payload())

    assert response.status_code == 401


def test_extract_returns_the_versioned_union_and_correlates_the_request() -> None:
    policy = FakePolicy()
    app = create_app(
        ExtractorSettings(environment="test", shared_secret="test-secret"),
        policy=policy,
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/extract",
            json=_request_payload(),
            headers={"X-Document-Extractor-Token": "test-secret"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "api-auth-test"
    assert response.json() == {
        "schema_version": 1,
        "request_id": "api-auth-test",
        "kind": "failure",
        "code": "no_content",
        "retryable": False,
        "http_status": None,
        "message": "No readable content was extracted",
        "timings": [],
    }
    assert policy.closed


def test_extract_rejects_an_oversized_request_before_validation() -> None:
    settings = ExtractorSettings(
        environment="test",
        shared_secret="test-secret",
        max_request_bytes=4_096,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/v1/extract",
            content=b"x" * 4_097,
            headers={
                "Content-Type": "application/json",
                "X-Document-Extractor-Token": "test-secret",
            },
        )

    assert response.status_code == 413
