import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from newsly_document_extractor.models import (
    SCHEMA_VERSION,
    ExtractIntent,
    ExtractOptions,
    ExtractRequest,
    TraceContext,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "newsly_document_extractor"
FORBIDDEN_IMPORT_ROOTS = {
    "alembic",
    "app",
    "asyncpg",
    "psycopg",
    "sqlalchemy",
}


def test_service_package_has_no_application_or_database_imports() -> None:
    imported_roots: set[str] = set()
    for source_path in PACKAGE_ROOT.glob("*.py"):
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])

    assert imported_roots.isdisjoint(FORBIDDEN_IMPORT_ROOTS)


def test_request_rejects_arbitrary_crawler_options() -> None:
    with pytest.raises(ValidationError):
        ExtractRequest.model_validate(
            {
                "schema_version": 1,
                "request_id": "fixture-request",
                "url": "https://example.com/article",
                "intent": "extract_article",
                "absolute_deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                "options": {"crawl4ai_javascript": "stealSecrets()"},
            }
        )


def test_request_has_a_bounded_policy_surface() -> None:
    request = ExtractRequest(
        schema_version=SCHEMA_VERSION,
        request_id="fixture-request",
        url="https://example.com/article",
        intent=ExtractIntent.EXTRACT_ARTICLE,
        absolute_deadline=datetime.now(UTC) + timedelta(seconds=30),
        options=ExtractOptions.defaults(),
        trace=TraceContext(trace_id=None, span_id=None),
    )

    assert request.schema_version == 1
    assert request.options.max_download_bytes == 5_000_000
    assert request.options.max_markdown_bytes == 1_000_000


def test_request_requires_every_wire_field() -> None:
    complete = {
        "schema_version": SCHEMA_VERSION,
        "request_id": "fixture-request",
        "url": "https://example.com/article",
        "intent": ExtractIntent.EXTRACT_ARTICLE,
        "absolute_deadline": datetime.now(UTC) + timedelta(seconds=30),
        "options": ExtractOptions.defaults().model_dump(mode="json"),
        "trace": TraceContext(trace_id=None, span_id=None).model_dump(mode="json"),
    }

    for field_name in complete:
        incomplete = {key: value for key, value in complete.items() if key != field_name}
        with pytest.raises(ValidationError):
            ExtractRequest.model_validate(incomplete)
