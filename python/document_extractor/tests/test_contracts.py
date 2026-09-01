import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from newsly_document_extractor.models import (
    EXTRACT_RESULT_ADAPTER,
    SCHEMA_VERSION,
    ExtractIntent,
    ExtractOptions,
    ExtractRequest,
    TraceContext,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "newsly_document_extractor"
EXTRACTION_CONTRACT_ROOT = Path(__file__).resolve().parents[3] / "contracts" / "extraction"
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


def test_language_neutral_golden_validates_every_result_variant() -> None:
    corpus = json.loads((EXTRACTION_CONTRACT_ROOT / "crawl4ai-golden.json").read_text())

    assert {case["expected"]["kind"] for case in corpus["cases"]} == {
        "success",
        "delegation",
        "fallback_required",
        "failure",
    }
    for case in corpus["cases"]:
        request = ExtractRequest.model_validate(case["request"])
        result = EXTRACT_RESULT_ADAPTER.validate_python(case["expected"])
        assert result.request_id == request.request_id, case["name"]


@pytest.mark.parametrize(
    ("schema_name", "actual"),
    [
        pytest.param(
            "crawl4ai-request.schema.json", ExtractRequest.model_json_schema(), id="request"
        ),
        pytest.param(
            "crawl4ai-result.schema.json", EXTRACT_RESULT_ADAPTER.json_schema(), id="result"
        ),
    ],
)
def test_checked_schemas_match_the_python_wire_models(
    schema_name: str, actual: dict[str, object]
) -> None:
    expected = json.loads((EXTRACTION_CONTRACT_ROOT / schema_name).read_text())
    actual["$schema"] = "https://json-schema.org/draft/2020-12/schema"

    assert actual == expected
