"""Private FastAPI surface for database-free document extraction."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse

from newsly_document_extractor.models import ExtractRequest, ExtractResult
from newsly_document_extractor.policy import ExtractionPolicy
from newsly_document_extractor.settings import ExtractorSettings, get_settings


class _RequestBodyTooLarge(RuntimeError):
    pass


class DocumentExtractionPolicy(Protocol):
    async def extract(self, request: ExtractRequest) -> ExtractResult: ...

    async def close(self) -> None: ...


class RequestSizeLimitMiddleware:
    """Reject declared and streamed request bodies above a hard byte limit."""

    def __init__(self, app: Any, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared_length = headers.get(b"content-length")
        if declared_length is not None:
            try:
                body_length = int(declared_length)
            except ValueError:
                await self._reject(
                    scope,
                    receive,
                    send,
                    status.HTTP_400_BAD_REQUEST,
                    "Invalid Content-Length",
                )
                return
            if body_length > self.max_bytes:
                await self._reject(
                    scope,
                    receive,
                    send,
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "Request body exceeds the configured byte limit",
                )
                return

        received_bytes = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received_bytes
            message = await receive()
            if message.get("type") == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await self._reject(
                scope,
                receive,
                send,
                status.HTTP_413_CONTENT_TOO_LARGE,
                "Request body exceeds the configured byte limit",
            )

    @staticmethod
    async def _reject(
        scope: dict[str, Any],
        receive: Any,
        send: Any,
        status_code: int,
        detail: str,
    ) -> None:
        response = JSONResponse({"detail": detail}, status_code=status_code)
        await response(scope, receive, send)


def create_app(
    settings: ExtractorSettings | None = None,
    *,
    policy: DocumentExtractionPolicy | None = None,
) -> FastAPI:
    process_settings = settings or get_settings()
    extraction_policy = policy or ExtractionPolicy(process_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await extraction_policy.close()

    app = FastAPI(
        title="Newsly Document Extractor",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=process_settings.max_request_bytes)

    def authorize(
        token: Annotated[str | None, Header(alias="X-Document-Extractor-Token")] = None,
    ) -> None:
        configured_secret = process_settings.shared_secret
        if configured_secret is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Document extractor authentication is not configured",
            )
        presented = token or ""
        if not hmac.compare_digest(configured_secret.get_secret_value(), presented):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Document extractor authentication failed",
            )

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, object]:
        return {"status": "ok", "schema_version": 1}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> dict[str, object]:
        # Browser creation is intentionally lazy. Configuration validation and event-loop
        # availability are sufficient for readiness; extraction failures stay typed per request.
        if process_settings.shared_secret is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Document extractor authentication is not configured",
            )
        return {"status": "ready", "schema_version": 1}

    @app.post(
        "/v1/extract",
        response_model=ExtractResult,
        response_model_exclude_none=False,
        dependencies=[Depends(authorize)],
    )
    async def extract(request: ExtractRequest, response: Response) -> ExtractResult:
        response.headers["X-Request-ID"] = request.request_id
        return await extraction_policy.extract(request)

    return app


app = create_app()
