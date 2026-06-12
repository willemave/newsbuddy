from __future__ import annotations

import pkgutil
from datetime import datetime
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin, get_type_hints

from fastapi.routing import APIRoute
from pydantic import BaseModel

import app.models.api as api_models_package
from app.main import app
from app.models.api.base import UTCDateTime
from app.models.contracts_registry import CONTRACT_UNTYPED_FIELD_ALLOWLIST

NO_BODY_OR_STREAMING_ROUTE_EXCEPTIONS = {
    ("GET", "/api/content/audio-episodes/{audio_episode_id}/audio"): "FileResponse audio bytes",
    ("GET", "/api/content/audio-episodes/{audio_episode_id}/stream"): (
        "StreamingResponse audio bytes"
    ),
    ("DELETE", "/api/content/chat/sessions/{session_id}"): "204 no-body delete",
    ("DELETE", "/api/content/scrapers/{config_id}"): "204 no-body delete",
    ("DELETE", "/api/learning/decks/{deck_id}"): "204 no-body delete",
    ("DELETE", "/api/scrapers/{config_id}"): "204 no-body delete",
}

IGNORED_OPENAPI_SCHEMAS = {"HTTPValidationError", "ValidationError"}


def test_api_routes_have_typed_response_models() -> None:
    """Every JSON API route should expose a concrete Pydantic response contract."""
    failures: list[str] = []
    seen_exceptions: set[tuple[str, str]] = set()

    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue

        methods = sorted(route.methods or [])
        route_keys = {(method, route.path) for method in methods}
        if route_keys & NO_BODY_OR_STREAMING_ROUTE_EXCEPTIONS.keys():
            seen_exceptions.update(route_keys & NO_BODY_OR_STREAMING_ROUTE_EXCEPTIONS.keys())
            continue

        if not _is_pydantic_response_model(route.response_model):
            failures.append(
                f"{','.join(methods)} {route.path} response_model={route.response_model!r}"
            )

    unused_exceptions = set(NO_BODY_OR_STREAMING_ROUTE_EXCEPTIONS) - seen_exceptions
    assert unused_exceptions == set(), "Remove stale response-model exceptions: " + ", ".join(
        sorted(map(str, unused_exceptions))
    )
    assert failures == []


def test_openapi_untyped_surface_matches_allowlist() -> None:
    """Free-form object fields must be reviewed through the checked-in allowlist."""
    schema = app.openapi()
    actual = sorted(_collect_untyped_openapi_properties(schema))
    allowlist = sorted(_load_untyped_allowlist())

    assert actual == allowlist


def test_api_datetime_fields_use_utc_datetime_serializer() -> None:
    """API DTO datetimes must use UTCDateTime so clients see one RFC3339 shape."""
    failures: list[str] = []

    for module_info in pkgutil.iter_modules(
        api_models_package.__path__,
        api_models_package.__name__ + ".",
    ):
        module = __import__(module_info.name, fromlist=["*"])
        for model in _module_base_models(module):
            type_hints = get_type_hints(model, include_extras=True)
            for field_name, annotation in type_hints.items():
                if field_name not in model.model_fields:
                    continue
                if _contains_datetime(annotation) and not _contains_utc_datetime(annotation):
                    failures.append(f"{model.__module__}.{model.__name__}.{field_name}")

    assert failures == []


def _is_pydantic_response_model(model: Any) -> bool:
    if model is None or model is dict or get_origin(model) is dict:
        return False
    if isinstance(model, type) and issubclass(model, BaseModel):
        return True

    origin = get_origin(model)
    if origin in {list, tuple, set, frozenset}:
        args = get_args(model)
        return bool(args) and all(_is_pydantic_response_model(arg) for arg in args)
    if origin in {UnionType, Union}:
        args = tuple(arg for arg in get_args(model) if arg is not type(None))
        return bool(args) and all(_is_pydantic_response_model(arg) for arg in args)
    return False


def _collect_untyped_openapi_properties(schema: dict[str, Any]) -> set[str]:
    untyped_properties: set[str] = set()
    schemas = schema.get("components", {}).get("schemas", {})
    for schema_name, schema_def in schemas.items():
        if schema_name in IGNORED_OPENAPI_SCHEMAS:
            continue
        properties = schema_def.get("properties") if isinstance(schema_def, dict) else None
        if not isinstance(properties, dict):
            continue
        for property_name, property_schema in properties.items():
            if _has_free_form_object(property_schema):
                untyped_properties.add(f"{schema_name}.{property_name}")
    return untyped_properties


def _has_free_form_object(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    if node.get("additionalProperties") is True:
        return True
    if (
        node.get("type") == "object"
        and "properties" not in node
        and "additionalProperties" not in node
    ):
        return True
    return any(
        _has_free_form_object(child)
        for key, value in node.items()
        if key != "$ref"
        for child in (value if isinstance(value, list) else [value])
    )


def _load_untyped_allowlist() -> set[str]:
    return set(CONTRACT_UNTYPED_FIELD_ALLOWLIST)


def _module_base_models(module: Any) -> list[type[BaseModel]]:
    return [
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and issubclass(value, BaseModel)
        and value is not BaseModel
    ]


def _contains_datetime(annotation: Any) -> bool:
    if annotation is datetime:
        return True
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        return bool(args) and _contains_datetime(args[0])
    return any(_contains_datetime(arg) for arg in get_args(annotation))


def _contains_utc_datetime(annotation: Any) -> bool:
    if annotation == UTCDateTime:
        return True
    return any(_contains_utc_datetime(arg) for arg in get_args(annotation))
