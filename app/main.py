# ruff: noqa: E501
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.admin_web.auth import router as admin_auth_router
from app.admin_web.router import router as admin_web_router
from app.core.compression import PathScopedGZipMiddleware
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
from app.routers import api_content, auth
from app.routers.api import (
    agent,
    audio_episodes,
    briefing,
    discovery,
    feedback,
    integrations,
    interactions,
    learning_decks,
    llm_tasks,
    news,
    onboarding,
    openai,
    scraper_configs,
    share_actions,
)
from app.utils.image_urls import IMAGE_VERSION_QUERY_PARAM

# Initialize
settings = get_settings()
logger = setup_logging()
ADMIN_STATIC_DIR = Path(__file__).resolve().parent / "admin_web" / "static"
VERSIONED_IMAGE_CACHE_CONTROL = "public, max-age=31536000, immutable"
MAX_LOGGABLE_REQUEST_BODY_BYTES = 64 * 1024
_LOGGABLE_REQUEST_CONTENT_TYPES = {
    "application/json",
    "application/x-www-form-urlencoded",
}

PUBLIC_HOME_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow, noarchive">
  <title>Newsbuddy</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; }
    body { margin: 0; background: #f7f5f1; color: #24221f; }
    main { max-width: 38rem; margin: 18vh auto; padding: 2rem; }
    h1 { margin: 0 0 0.75rem; font-size: 2.25rem; }
    p { color: #5d5952; line-height: 1.6; }
    a { color: #8a4b2b; }
  </style>
</head>
<body>
  <main>
    <h1>Newsbuddy</h1>
    <p>A private news reading and learning service.</p>
    <p><a href="/privacy">Privacy</a> · <a href="/support">Support</a> · <a href="/terms">Terms</a> · <a href="/health">Service status</a></p>
  </main>
</body>
</html>
"""
PUBLIC_DOCUMENT_STYLE = """
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; }
    body { margin: 0; background: #f7f5f1; color: #24221f; }
    main { max-width: 46rem; margin: 4rem auto; padding: 0 1.5rem 4rem; }
    h1, h2 { line-height: 1.2; } h2 { margin-top: 2rem; }
    p, li { color: #4f4b46; line-height: 1.65; }
    a { color: #7b4328; } nav { margin-bottom: 2rem; }
  </style>
"""
PRIVACY_HTML = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Newsbuddy Privacy Policy</title>
{PUBLIC_DOCUMENT_STYLE}</head><body><main><nav><a href="/">Newsbuddy</a></nav>
<h1>Privacy Policy</h1><p>Effective August 1, 2026.</p>
<p>Newsbuddy is a personal news reading and learning service. This policy explains the data the service processes to provide the app.</p>
<h2>Data we process</h2><ul>
<li>Apple account identifiers, name, and email supplied through Sign in with Apple.</li>
<li>Articles, links, feeds, X bookmarks, prompts, chats, voice transcripts, preferences, and feedback you submit or choose to synchronize.</li>
<li>Generated summaries, briefings, learning materials, images, and audio.</li>
<li>Operational records such as request identifiers, error details, task status, and provider usage needed to operate and secure the service.</li>
</ul>
<h2>External processing</h2><p>To provide requested features, Newsbuddy may send relevant content and instructions to service providers for artificial-intelligence processing, search and retrieval, transcription, speech, image generation, web extraction, hosting, and error monitoring. Providers currently used by configured features can include OpenAI, Anthropic, Google, Cerebras, OpenRouter, ElevenLabs, Exa, E2B, Firecrawl, Runware, Sentry, Cloudflare, and X. Only data needed for the requested operation is sent.</p>
<h2>X synchronization</h2><p>If you connect X, Newsbuddy stores encrypted OAuth credentials on its server and periodically imports your bookmarks in the background. You can disconnect X in Settings. Disconnecting stops future synchronization and revokes the connection; deleting your Newsbuddy account also removes the connection and associated credentials.</p>
<h2>Retention and deletion</h2><p>Data is retained while your account is active and as needed to operate requested features. You can delete your account in the app. Deletion deactivates access, revokes connected services, cancels pending work, and removes account-linked records and files, subject to short-lived backups and legal obligations.</p>
<h2>Your choices</h2><p>You control whether to connect X, submit voice recordings, or use features that send content to external processors. You may disconnect X or delete your account from Settings.</p>
<h2>Contact</h2><p>Questions may be sent to <a href="mailto:willem.ave@gmail.com">willem.ave@gmail.com</a>.</p>
</main></body></html>"""
SUPPORT_HTML = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Newsbuddy Support</title>
{PUBLIC_DOCUMENT_STYLE}</head><body><main><nav><a href="/">Newsbuddy</a></nav>
<h1>Support</h1><p>For help with Newsbuddy, email <a href="mailto:willem.ave@gmail.com">willem.ave@gmail.com</a>.</p>
<h2>Account and integrations</h2><p>Sign in with Apple is required. X can be connected or disconnected from Settings. Account deletion is available in Settings under Account.</p>
<h2>Processing time</h2><p>New articles, bookmarks, briefings, and learning materials may take several minutes to prepare. The app shows their processing state while work is underway.</p>
</main></body></html>"""
TERMS_HTML = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Newsbuddy Terms</title>
{PUBLIC_DOCUMENT_STYLE}</head><body><main><nav><a href="/">Newsbuddy</a></nav>
<h1>Terms of Use</h1><p>Effective August 1, 2026.</p>
<p>Newsbuddy provides personal tools for collecting, summarizing, and learning from content you choose. You remain responsible for the links, feeds, accounts, and instructions you submit and for complying with applicable laws and third-party terms.</p>
<p>Generated material may be incomplete or inaccurate and should not be relied on as professional advice. The service may change or be unavailable, and abusive or unlawful use may result in account suspension.</p>
<p>You may stop using the service and delete your account at any time from Settings. Questions may be sent to <a href="mailto:willem.ave@gmail.com">willem.ave@gmail.com</a>.</p>
</main></body></html>"""
PRIVATE_ORIGIN_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}


def _resolve_static_mount_paths() -> tuple[Path, Path]:
    """Create generated image storage and resolve packaged admin static assets."""
    images_dir = settings.images_base_dir.resolve()
    images_dir.mkdir(parents=True, exist_ok=True)

    return images_dir, ADMIN_STATIC_DIR


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize and teardown application services."""
    logger.info("Starting up...")
    init_db()
    logger.info("Database initialized")
    yield


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
    payload_summary = getattr(
        request.state,
        "request_payload_summary",
        {"shape": "unavailable"},
    )

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


def _static_image_cache_control(path: str, query_param_keys: set[str]) -> str | None:
    """Return cache-control for generated image responses."""
    if not path.startswith("/static/images/"):
        return None
    if IMAGE_VERSION_QUERY_PARAM not in query_param_keys:
        return None
    return VERSIONED_IMAGE_CACHE_CONTROL


def _normalized_content_type(content_type: str | None) -> str:
    """Return a media type without parameters."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def _declared_content_length(request: Request) -> int | None:
    """Return a valid declared body size when the request provides one."""
    raw_content_length = request.headers.get("content-length")
    if raw_content_length is None:
        return None
    try:
        content_length = int(raw_content_length)
    except ValueError:
        return None
    return content_length if content_length >= 0 else None


def _request_body_logging_skip_reason(
    *,
    content_type: str | None,
    content_length: int | None,
) -> str | None:
    """Return why the middleware must not buffer a request body, if applicable."""
    normalized_type = _normalized_content_type(content_type)
    if content_length == 0:
        return "empty"
    if normalized_type.startswith("multipart/"):
        return "multipart"
    if normalized_type == "application/octet-stream":
        return "binary"
    if normalized_type not in _LOGGABLE_REQUEST_CONTENT_TYPES:
        return "unsupported_content_type"
    if content_length is None:
        return "unknown_size"
    if content_length > MAX_LOGGABLE_REQUEST_BODY_BYTES:
        return "size_limit"
    return None


async def _request_payload_summary(
    request: Request,
) -> tuple[bytes | None, dict[str, object]]:
    """Summarize a small structured body without buffering upload or large bodies."""
    content_type = request.headers.get("content-type")
    content_length = _declared_content_length(request)
    skip_reason = _request_body_logging_skip_reason(
        content_type=content_type,
        content_length=content_length,
    )
    if skip_reason is not None:
        return None, {
            "body_bytes": content_length,
            "content_type": content_type,
            "shape": "not_inspected",
            "reason": skip_reason,
        }

    try:
        body_bytes = await request.body()
    except Exception:
        return None, {
            "body_bytes": content_length,
            "content_type": content_type,
            "shape": "unavailable",
        }
    return body_bytes, summarize_request_payload(body_bytes, content_type)


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

    content_type = request.headers.get("content-type")
    content_length = _declared_content_length(request)
    body_bytes, payload_summary = await _request_payload_summary(request)
    request.state.request_payload_summary = payload_summary

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
        if cache_control := _static_image_cache_control(
            path,
            set(request.query_params.keys()),
        ):
            response.headers["Cache-Control"] = cache_control

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
                    "request_bytes": (
                        len(body_bytes) if body_bytes is not None else content_length
                    ),
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

# Briefing lens payloads are large text-heavy JSON; audio streaming and SSE
# routes stay uncompressed.
app.add_middleware(
    PathScopedGZipMiddleware,
    path_prefixes=("/api/briefing",),
    minimum_size=1024,
)

# Mount generated content images and packaged admin web assets.
images_static_dir, admin_static_dir = _resolve_static_mount_paths()
app.mount("/static/images", StaticFiles(directory=images_static_dir), name="static-images")
app.mount("/admin/static", StaticFiles(directory=admin_static_dir), name="admin-static")

# Include routers
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(admin_auth_router)
app.include_router(admin_web_router)
app.include_router(api_content.router, prefix="/api/content")
app.include_router(news.router, prefix="/api/news")
app.include_router(learning_decks.router, prefix="/api")
app.include_router(learning_decks.public_router)
app.include_router(audio_episodes.public_router)
app.include_router(briefing.router, prefix="/api")
app.include_router(interactions.router, prefix="/api")
app.include_router(feedback.router, prefix="/api")
app.include_router(scraper_configs.router, prefix="/api")
app.include_router(discovery.router, prefix="/api")
app.include_router(onboarding.router, prefix="/api")
app.include_router(integrations.router, prefix="/api")
app.include_router(integrations.llm_router, prefix="/api")
app.include_router(llm_tasks.router, prefix="/api")
app.include_router(share_actions.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(openai.router, prefix="/api")


@app.get("/", include_in_schema=False, response_class=HTMLResponse)
async def public_home() -> HTMLResponse:
    """Describe the private service without exposing its admin sign-in flow."""
    return HTMLResponse(PUBLIC_HOME_HTML, headers=PRIVATE_ORIGIN_HEADERS)


@app.get("/privacy", include_in_schema=False, response_class=HTMLResponse)
async def privacy_policy() -> HTMLResponse:
    """Publish the privacy policy required by the public clients and stores."""
    return HTMLResponse(PRIVACY_HTML, headers=PRIVATE_ORIGIN_HEADERS)


@app.get("/support", include_in_schema=False, response_class=HTMLResponse)
async def support_page() -> HTMLResponse:
    """Publish public support and account-management guidance."""
    return HTMLResponse(SUPPORT_HTML, headers=PRIVATE_ORIGIN_HEADERS)


@app.get("/terms", include_in_schema=False, response_class=HTMLResponse)
async def terms_page() -> HTMLResponse:
    """Publish the service terms."""
    return HTMLResponse(TERMS_HTML, headers=PRIVATE_ORIGIN_HEADERS)


@app.get("/robots.txt", include_in_schema=False, response_class=PlainTextResponse)
async def robots_txt() -> PlainTextResponse:
    """Keep private API, admin, and signed viewer routes out of search indexes."""
    return PlainTextResponse(
        "User-agent: *\nDisallow: /\n",
        headers=PRIVATE_ORIGIN_HEADERS,
    )


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
