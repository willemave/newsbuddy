from __future__ import annotations

from app.main import app
from scripts.export_agent_openapi_schema import build_agent_openapi_schema


def test_openapi_operation_ids_are_unique() -> None:
    schema = app.openapi()

    operation_ids = [
        operation["operationId"]
        for path_item in schema["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]

    assert operation_ids
    assert len(operation_ids) == len(set(operation_ids))


def test_openapi_emits_stable_operation_ids_for_selected_routes() -> None:
    schema = app.openapi()

    assert schema["paths"]["/api/agent/digests"]["post"]["operationId"] == "generateDigest"
    assert (
        schema["paths"]["/api/agent/library/manifest"]["get"]["operationId"]
        == "getAgentLibraryManifest"
    )
    assert schema["paths"]["/api/scrapers/"]["get"]["operationId"] == "listScraperConfigs"
    assert (
        schema["paths"]["/api/content/scrapers/"]["get"]["operationId"]
        == "listContentScraperConfigs"
    )


def test_agent_openapi_schema_includes_cli_auth_and_library_routes() -> None:
    schema = build_agent_openapi_schema()

    assert schema["paths"]["/api/agent/cli/link/start"]["post"]["operationId"] == "startCliLink"
    assert (
        schema["paths"]["/api/agent/library/manifest"]["get"]["operationId"]
        == "getAgentLibraryManifest"
    )
    assert schema["paths"]["/api/agent/digests"]["post"]["tags"] == ["news"]
    assert {tag["name"] for tag in schema["tags"]} >= {"auth", "library", "news"}
