"""Tests for public absolute URL construction."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.external_urls import external_url_for


@pytest.fixture
def url_app() -> FastAPI:
    app = FastAPI()

    @app.get("/learning/signed/{token}/", name="signed_deck")
    def signed_deck(token: str, request: Request) -> dict[str, str]:
        return {"url": external_url_for(request, "signed_deck", token=token)}

    return app


def test_external_url_uses_public_origin_behind_internal_http_proxy(
    url_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.external_urls.get_settings",
        lambda: SimpleNamespace(public_base_url="https://public.example.com"),
    )

    response = TestClient(url_app, base_url="http://internal-api:8000").get(
        "/learning/signed/opaque-token/",
        headers={"host": "untrusted-client-host.example"},
    )

    assert response.status_code == 200
    assert response.json()["url"] == ("https://public.example.com/learning/signed/opaque-token/")


def test_external_url_uses_request_origin_for_local_development(
    url_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.external_urls.get_settings",
        lambda: SimpleNamespace(public_base_url=None),
    )

    response = TestClient(url_app, base_url="http://localhost:8000").get(
        "/learning/signed/local-token/"
    )

    assert response.status_code == 200
    assert response.json()["url"] == "http://localhost:8000/learning/signed/local-token/"


def test_docker_gateway_proxy_headers_restore_forwarded_https_scheme(
    url_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.external_urls.get_settings",
        lambda: SimpleNamespace(public_base_url=None),
    )
    proxied_app = ProxyHeadersMiddleware(
        url_app,  # type: ignore[arg-type]  # Uvicorn and Starlette ASGI aliases differ.
        trusted_hosts="127.0.0.1,172.16.0.0/12",
    )

    response = TestClient(
        proxied_app,  # type: ignore[arg-type]  # Uvicorn and Starlette ASGI aliases differ.
        base_url="http://internal-api:8000",
        client=("172.18.0.1", 40_000),
    ).get(
        "/learning/signed/proxied-token/",
        headers={
            "host": "public.example.com",
            "x-forwarded-proto": "https",
        },
    )

    assert response.status_code == 200
    assert response.json()["url"] == ("https://public.example.com/learning/signed/proxied-token/")
