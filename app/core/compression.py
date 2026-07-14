"""Path-scoped response compression.

Briefing lens payloads are large text-heavy JSON (~60KB) that gzips to a
fraction of the size; audio streaming and SSE routes must stay untouched, so
compression is applied per path prefix instead of globally.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger
from app.core.observability import build_log_extra

logger = get_logger(__name__)

BRIEFING_GZIP_COMPRESS_LEVEL = 9


@dataclass
class _ResponseBodySizes:
    uncompressed_bytes: int = 0
    compressed_bytes: int = 0


_active_response_sizes: ContextVar[_ResponseBodySizes | None] = ContextVar(
    "briefing_compression_response_sizes",
    default=None,
)


class _UncompressedBodyMeasuringApp:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        sizes = _active_response_sizes.get()

        async def measuring_send(message: Message) -> None:
            if sizes is not None and message["type"] == "http.response.body":
                sizes.uncompressed_bytes += len(message.get("body", b""))
            await send(message)

        await self.app(scope, receive, measuring_send)


class PathScopedGZipMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        path_prefixes: tuple[str, ...],
        minimum_size: int = 1024,
    ) -> None:
        self.app = app
        self.gzip_app = GZipMiddleware(
            _UncompressedBodyMeasuringApp(app),
            minimum_size=minimum_size,
            compresslevel=BRIEFING_GZIP_COMPRESS_LEVEL,
        )
        self.path_prefixes = path_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith(self.path_prefixes):
            sizes = _ResponseBodySizes()
            token = _active_response_sizes.set(sizes)

            async def measuring_send(message: Message) -> None:
                if message["type"] == "http.response.body":
                    sizes.compressed_bytes += len(message.get("body", b""))
                await send(message)

            try:
                await self.gzip_app(scope, receive, measuring_send)
            finally:
                _active_response_sizes.reset(token)
            logger.info(
                "Briefing response compression measured",
                extra=build_log_extra(
                    component="briefing",
                    operation="compress_response",
                    event_name="briefing.response.compression",
                    status="completed",
                    http_details={
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                    },
                    context_data={
                        "uncompressed_bytes": sizes.uncompressed_bytes,
                        "compressed_bytes": sizes.compressed_bytes,
                    },
                ),
            )
            return
        await self.app(scope, receive, send)
