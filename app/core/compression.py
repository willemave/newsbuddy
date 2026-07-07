"""Path-scoped response compression.

Briefing lens payloads are large text-heavy JSON (~60KB) that gzips to a
fraction of the size; audio streaming and SSE routes must stay untouched, so
compression is applied per path prefix instead of globally.
"""

from __future__ import annotations

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


class PathScopedGZipMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        path_prefixes: tuple[str, ...],
        minimum_size: int = 1024,
    ) -> None:
        self.app = app
        self.gzip_app = GZipMiddleware(app, minimum_size=minimum_size)
        self.path_prefixes = path_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith(self.path_prefixes):
            await self.gzip_app(scope, receive, send)
            return
        await self.app(scope, receive, send)
