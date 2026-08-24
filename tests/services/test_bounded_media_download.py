from __future__ import annotations

from pathlib import Path

import pytest

from app.services import bounded_media_download
from app.services.bounded_media_download import (
    MAX_REMOTE_MEDIA_BYTES,
    BoundedMediaDownloadError,
    download_remote_media_bounded,
)


class _FakeHttpService:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def download_bounded_public(
        self,
        url: str,
        destination: str,
        **kwargs,
    ) -> None:
        self.calls.append({"url": url, "destination": destination, **kwargs})
        Path(destination).write_bytes(b"podcast-bytes")
        if self.failure is not None:
            raise self.failure


def test_bounded_media_download_is_atomic_and_has_explicit_limits(
    monkeypatch,
    tmp_path: Path,
) -> None:
    http_service = _FakeHttpService()
    monkeypatch.setattr(bounded_media_download, "get_http_service", lambda: http_service)
    destination = tmp_path / "episode.mp3"

    result = download_remote_media_bounded(
        "https://cdn.example/episode.mp3",
        destination,
    )

    assert result == destination
    assert destination.read_bytes() == b"podcast-bytes"
    call = http_service.calls[0]
    assert call["max_response_bytes"] == MAX_REMOTE_MEDIA_BYTES
    assert call["max_redirects"] == 10
    assert str(call["destination"]).endswith(".partial")
    assert list(tmp_path.glob("*.partial")) == []


def test_bounded_media_download_cleans_partial_and_preserves_existing_destination(
    monkeypatch,
    tmp_path: Path,
) -> None:
    http_service = _FakeHttpService(failure=ValueError("private address blocked"))
    monkeypatch.setattr(bounded_media_download, "get_http_service", lambda: http_service)
    destination = tmp_path / "episode.mp3"
    destination.write_bytes(b"existing")

    with pytest.raises(BoundedMediaDownloadError, match="Bounded media download failed"):
        download_remote_media_bounded(
            "http://169.254.169.254/private.mp3",
            destination,
        )

    assert destination.read_bytes() == b"existing"
    assert list(tmp_path.glob("*.partial")) == []
