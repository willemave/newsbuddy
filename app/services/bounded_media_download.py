"""SSRF-safe bounded host download for remote podcast media."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.services.http import get_http_service

MAX_REMOTE_MEDIA_BYTES = 500_000_000


class BoundedMediaDownloadError(RuntimeError):
    """Raised when bounded host media download cannot be completed."""


def download_remote_media_bounded(url: str, destination: Path) -> Path:
    token = uuid4().hex
    partial_path = destination.with_name(f".{destination.name}.{token}.partial")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        get_http_service().download_bounded_public(
            url,
            str(partial_path),
            max_response_bytes=MAX_REMOTE_MEDIA_BYTES,
            max_redirects=10,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NewsAggregator/1.0; Podcast Downloader)"
            },
        )
        partial_path.replace(destination)
    except Exception as exc:  # noqa: BLE001
        raise BoundedMediaDownloadError("Bounded media download failed") from exc
    finally:
        partial_path.unlink(missing_ok=True)
    return destination
