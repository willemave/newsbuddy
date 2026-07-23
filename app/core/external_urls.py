"""Construction of absolute URLs that leave the Newsly API boundary."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from fastapi import Request

from app.core.settings import get_settings


def external_url_for(
    request: Request,
    route_name: str,
    **path_params: str | int,
) -> str:
    """Build a route URL on the configured public origin when one is available.

    Local development intentionally falls back to Starlette's request-derived
    origin. Production requires ``PUBLIC_BASE_URL``, so externally returned URLs
    do not depend on reverse-proxy header interpretation.
    """

    route_url = request.url_for(route_name, **path_params)
    public_base_url = get_settings().public_base_url
    if public_base_url is None:
        return str(route_url)

    public_origin = urlsplit(str(public_base_url))
    return urlunsplit(
        (
            public_origin.scheme,
            public_origin.netloc,
            route_url.path,
            route_url.query,
            route_url.fragment,
        )
    )
