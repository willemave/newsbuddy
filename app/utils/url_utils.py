"""URL normalization helpers."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_SCHEME_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _normalized_hostname(value: str | None) -> str:
    """Return a comparison-safe DNS hostname."""
    return (value or "").strip().lower().rstrip(".")


def is_domain_or_subdomain(hostname: str | None, domain: str) -> bool:
    """Return whether ``hostname`` is ``domain`` or one of its subdomains."""
    normalized_host = _normalized_hostname(hostname)
    normalized_domain = _normalized_hostname(domain)
    if not normalized_host or not normalized_domain:
        return False
    return normalized_host == normalized_domain or normalized_host.endswith(f".{normalized_domain}")


def is_http_url(value: str | None) -> bool:
    """Return True when value is a valid http(s) URL."""
    if not value or not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value.strip())
        hostname = parsed.hostname
        _ = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(hostname)
        and not any(character.isspace() for character in hostname or "")
    )


def normalize_http_url(value: str | None) -> str | None:
    """Normalize a URL to https and strip whitespace, returning None when invalid."""
    if not value or not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.startswith("//"):
        candidate = f"https:{cleaned}"
    elif cleaned.startswith(("/", "./", "../", "?", "#")):
        return None
    elif _SCHEME_PREFIX.match(cleaned):
        # ``urlparse`` treats ``example.com:8080`` as a custom scheme. Preserve
        # common schemeless host:port input without accepting arbitrary schemes.
        prefix, remainder = cleaned.split(":", 1)
        port = remainder.split("/", 1)[0]
        if not port.isdigit() or ("." not in prefix and prefix.lower() != "localhost"):
            candidate = cleaned
        else:
            candidate = f"https://{cleaned}"
    else:
        candidate = f"https://{cleaned}"

    try:
        parsed = urlparse(candidate)
        hostname = parsed.hostname
        _ = parsed.port
    except (TypeError, ValueError):
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return None
    if any(character.isspace() for character in hostname):
        return None
    return parsed._replace(scheme="https").geturl()
