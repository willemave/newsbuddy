import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.core.db import get_engine, init_db
from app.core.deps import AdminAuthRequired
from app.core.logging import setup_logging
from app.core.observability import (
    bound_log_context,
    build_log_extra,
    summarize_headers,
    summarize_request_payload,
)
from app.core.settings import get_settings
from app.openapi import build_operation_id
from app.routers import admin, api_content, auth, logs
from app.routers.api import (
    agent,
    discovery,
    integrations,
    interactions,
    news,
    onboarding,
    openai,
    scraper_configs,
)
from app.services.langfuse_tracing import (
    flush_langfuse_tracing,
    initialize_langfuse_tracing,
    langfuse_trace_context,
)

# Initialize
settings = get_settings()
logger = setup_logging()


def _ensure_static_mount_directories() -> tuple[Path, Path]:
    """Create local static mount directories before Starlette validates them."""
    images_dir = settings.storage.images_base_dir.resolve()
    images_dir.mkdir(parents=True, exist_ok=True)

    static_dir = Path("static").resolve()
    static_dir.mkdir(parents=True, exist_ok=True)

    return images_dir, static_dir


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize and teardown application services."""
    logger.info("Starting up...")
    initialize_langfuse_tracing()
    init_db()
    logger.info("Database initialized")
    try:
        yield
    finally:
        flush_langfuse_tracing()


# Create app
app = FastAPI(
    title=settings.app_name,
    version="2.0.0",
    description="Unified News Aggregation System",
    generate_unique_id_function=build_operation_id,
    lifespan=lifespan,
)


# Exception handlers
def _serialize_validation_errors(
    errors: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Convert validation errors to JSON-serializable format."""
    serialized = []
    for error in errors:
        serialized_error = {
            "loc": error.get("loc"),
            "msg": str(error.get("msg", "")),
            "type": error.get("type"),
        }
        # Only include input if it's JSON-serializable
        if "input" in error:
            try:
                import json

                json.dumps(error["input"])
                serialized_error["input"] = error["input"]
            except (TypeError, ValueError):
                serialized_error["input"] = str(error["input"])
        serialized.append(serialized_error)
    return serialized


def _route_details(request: Request) -> tuple[str | None, str | None]:
    """Return the matched route name and template path when available."""
    route = request.scope.get("route")
    if route is None:
        return None, None
    return getattr(route, "name", None), getattr(route, "path", None)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Handle Pydantic validation errors with detailed logging.

    This catches 422 errors before they reach endpoint code.
    """
    # Get raw body for logging
    body = None
    try:
        body = await request.body()
    except Exception:
        payload_summary = {"shape": "unavailable"}
    else:
        payload_summary = summarize_request_payload(body, request.headers.get("content-type"))

    route_name, route_path = _route_details(request)
    logger.error(
        "Request validation failed",
        extra=build_log_extra(
            component="http",
            operation="request_validation",
            event_name="http.request",
            status="validation_failed",
            request_id=getattr(request.state, "request_id", None),
            user_id=getattr(request.state, "authenticated_user_id", None),
            http_details={
                "method": request.method,
                "path": request.url.path,
                "route_name": route_name,
                "route_path": route_path,
                "client_ip": request.client.host if request.client else None,
                "query_param_keys": sorted(request.query_params.keys()),
                "header_summary": summarize_headers(dict(request.headers)),
                "payload_summary": payload_summary,
            },
            context_data={
                "error_count": len(exc.errors()),
                "errors": [
                    {
                        "loc": list(error.get("loc", ())),
                        "msg": str(error.get("msg", "")),
                        "type": error.get("type"),
                    }
                    for error in exc.errors()
                ],
            },
        ),
    )

    # Return standard FastAPI validation error response with serialized errors
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": _serialize_validation_errors(exc.errors())},
    )


@app.exception_handler(AdminAuthRequired)
async def admin_auth_redirect_handler(_request: Request, exc: AdminAuthRequired):
    """Redirect to admin login page when admin authentication is required."""
    return RedirectResponse(url=exc.redirect_url, status_code=status.HTTP_303_SEE_OTHER)


# Paths to skip in request logging (high-frequency polling endpoints)
SKIP_LOG_PATHS = {"/health", "/api/content/chat/messages", "/api/content/unread-counts"}


def _should_skip_logging(path: str) -> bool:
    """Check if request path should skip logging (status polling etc)."""
    return any(path.startswith(skip_path) for skip_path in SKIP_LOG_PATHS)


# Request logging middleware with timing
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming HTTP requests with timing information."""
    start_time = time.perf_counter()
    path = request.url.path
    skip_logging = _should_skip_logging(path)
    request_id = request.headers.get("X-Request-ID") or uuid4().hex
    request.state.request_id = request_id
    route_name, route_path = _route_details(request)

    body_bytes: bytes | None = None
    payload_summary: dict[str, object] | None = None
    content_type = request.headers.get("content-type")
    try:
        body_bytes = await request.body()
    except Exception:
        body_bytes = None
    else:
        payload_summary = summarize_request_payload(body_bytes, content_type)

    with bound_log_context(request_id=request_id, source="http"):
        if not skip_logging:
            logger.info(
                "HTTP request started",
                extra=build_log_extra(
                    component="http",
                    operation="request",
                    event_name="http.request",
                    status="started",
                    request_id=request_id,
                    user_id=getattr(request.state, "authenticated_user_id", None),
                    http_details={
                        "method": request.method,
                        "path": path,
                        "route_name": route_name,
                        "route_path": route_path,
                        "query_param_keys": sorted(request.query_params.keys()),
                        "client_ip": request.client.host if request.client else None,
                        "user_agent": request.headers.get("user-agent"),
                        "content_type": content_type,
                        "auth_present": bool(request.headers.get("authorization")),
                        "payload_summary": payload_summary,
                    },
                ),
            )

        try:
            with langfuse_trace_context(
                trace_name=f"http.{request.method.lower()}",
                metadata={
                    "source": "realtime",
                    "path": path,
                    "method": request.method,
                    "request_id": request_id,
                },
                tags=["realtime", "http"],
            ):
                response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.exception(
                "HTTP request failed",
                extra=build_log_extra(
                    component="http",
                    operation="request",
                    event_name="http.request",
                    status="failed",
                    duration_ms=duration_ms,
                    request_id=request_id,
                    user_id=getattr(request.state, "authenticated_user_id", None),
                    http_details={
                        "method": request.method,
                        "path": path,
                        "route_name": route_name,
                        "route_path": route_path,
                        "query_param_keys": sorted(request.query_params.keys()),
                        "client_ip": request.client.host if request.client else None,
                        "content_type": content_type,
                    },
                    context_data={"error_type": type(exc).__name__},
                ),
            )
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000
        response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"
        response.headers["X-Request-ID"] = request_id

        if skip_logging:
            return response

        logger_method = logger.info if duration_ms < 500 else logger.warning
        logger_method(
            "HTTP request completed",
            extra=build_log_extra(
                component="http",
                operation="request",
                event_name="http.request",
                status="completed",
                duration_ms=duration_ms,
                request_id=request_id,
                user_id=getattr(request.state, "authenticated_user_id", None),
                http_details={
                    "method": request.method,
                    "path": path,
                    "route_name": route_name,
                    "route_path": route_path,
                    "query_param_keys": sorted(request.query_params.keys()),
                    "status_code": response.status_code,
                    "client_ip": request.client.host if request.client else None,
                    "user_agent": request.headers.get("user-agent"),
                    "content_type": content_type,
                    "request_bytes": len(body_bytes or b""),
                    "response_bytes": response.headers.get("content-length"),
                    "auth_present": bool(request.headers.get("authorization")),
                },
            ),
        )

        return response


# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files (images first so they bypass repo static)
images_static_dir, repo_static_dir = _ensure_static_mount_directories()
app.mount("/static/images", StaticFiles(directory=images_static_dir), name="static-images")
app.mount("/static", StaticFiles(directory=repo_static_dir), name="static")

# Include routers
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(admin.router)
app.include_router(logs.router)
app.include_router(api_content.router, prefix="/api/content")
app.include_router(news.router, prefix="/api/news")
app.include_router(interactions.router, prefix="/api")
app.include_router(scraper_configs.router, prefix="/api")
app.include_router(discovery.router, prefix="/api")
app.include_router(onboarding.router, prefix="/api")
app.include_router(integrations.router, prefix="/api")
app.include_router(integrations.llm_router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(openai.router, prefix="/api")


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect root path to admin dashboard."""
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


def _check_database_health() -> None:
    """Run a lightweight database round-trip for readiness checks."""
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))


# Health check
@app.get("/health")
async def health_check():
    try:
        _check_database_health()
    except Exception:
        logger.exception(
            "Health check failed",
            extra=build_log_extra(
                component="health",
                operation="readiness",
                event_name="health.readiness",
                status="failed",
            ),
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "service": settings.app_name,
                "checks": {"database": "unhealthy"},
            },
        )

    return {
        "status": "healthy",
        "service": settings.app_name,
        "checks": {"database": "healthy"},
    }


if __name__ == "__main__":
    import os

    import uvicorn

    # Check if SSL certificates exist
    cert_file = "certs/cert.pem"
    key_file = "certs/key.pem"

    if os.path.exists(cert_file) and os.path.exists(key_file):
        # Run with HTTPS
        uvicorn.run(app, host="0.0.0.0", port=8000, ssl_certfile=cert_file, ssl_keyfile=key_file)
    else:
        # Run without HTTPS
        uvicorn.run(app, host="0.0.0.0", port=8000)
