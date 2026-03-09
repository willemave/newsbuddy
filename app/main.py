import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.db import init_db
from app.core.deps import AdminAuthRequired
from app.core.logging import setup_logging
from app.core.settings import get_settings
from app.routers import admin, api_content, auth, logs
from app.routers.api import (
    agent,
    discovery,
    integrations,
    interactions,
    onboarding,
    openai,
    scraper_configs,
    voice,
)
from app.services.langfuse_tracing import (
    flush_langfuse_tracing,
    initialize_langfuse_tracing,
    langfuse_trace_context,
)

# Initialize
settings = get_settings()
logger = setup_logging()


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
    lifespan=lifespan,
)


# Exception handlers
def _serialize_validation_errors(errors: list) -> list:
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


def _redact_request_headers(headers: dict[str, str]) -> dict[str, str]:
    """Redact sensitive request headers before logging."""
    sensitive_headers = {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "proxy-authorization",
    }
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in sensitive_headers:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def _summarize_request_body(body: bytes | None, *, max_chars: int = 512) -> str:
    """Return a bounded, text-safe request body summary for logs."""
    if not body:
        return "<empty>"
    decoded = body.decode("utf-8", errors="replace")
    if len(decoded) <= max_chars:
        return decoded
    return f"{decoded[:max_chars]}... <truncated>"


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
    except Exception as e:
        body_text = f"<unable to read body: {e}>"
    else:
        body_text = _summarize_request_body(body)

    # Log detailed validation error
    logger.error("=" * 80)
    logger.error("VALIDATION ERROR - Request failed Pydantic validation")
    logger.error(f"Path: {request.method} {request.url.path}")
    logger.error(f"Client: {request.client.host if request.client else 'unknown'}")
    logger.error(f"Headers: {_redact_request_headers(dict(request.headers))}")
    logger.error(f"Request body: {body_text}")
    logger.error("Validation errors:")
    for error in exc.errors():
        logger.error(f"  - Field: {error['loc']}, Error: {error['msg']}, Type: {error['type']}")
    logger.error("=" * 80)

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

    if not skip_logging:
        logger.info(f">>> {request.method} {path}")
        logger.debug(f"    Headers: {dict(request.headers)}")
        logger.debug(f"    Client: {request.client.host if request.client else 'unknown'}")

    with langfuse_trace_context(
        trace_name=f"http.{request.method.lower()}",
        metadata={
            "source": "realtime",
            "path": path,
            "method": request.method,
        },
        tags=["realtime", "http"],
    ):
        response = await call_next(request)

    duration_ms = (time.perf_counter() - start_time) * 1000

    # Add timing header to response
    response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"

    if skip_logging:
        return response

    # Log with severity based on duration
    method = request.method
    status_code = response.status_code
    time_str = f"{duration_ms:.2f}ms"

    if duration_ms < 100:
        logger.info(f"<<< {method} {path} - {status_code} [{time_str}]")
    elif duration_ms < 500:
        logger.info(f"<<< {method} {path} - {status_code} [{time_str}] (slow)")
    else:
        logger.warning(f"<<< {method} {path} - {status_code} [{time_str}] (very slow)")

    return response


# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files (images first so they bypass repo static)
app.mount("/static/images", StaticFiles(directory=settings.images_base_dir), name="static-images")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(admin.router)
app.include_router(logs.router)
app.include_router(api_content.router, prefix="/api/content")
app.include_router(interactions.router, prefix="/api")
app.include_router(scraper_configs.router, prefix="/api")
app.include_router(discovery.router, prefix="/api")
app.include_router(onboarding.router, prefix="/api")
app.include_router(integrations.router, prefix="/api")
app.include_router(integrations.llm_router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(openai.router, prefix="/api")
app.include_router(voice.router, prefix="/api")


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect root path to admin dashboard."""
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


# Health check
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": settings.app_name}


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
