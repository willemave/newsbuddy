from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.services import audio_pipeline
from app.services.audio_pipeline import (
    YtDlpDownloadDeadlineExceeded,
    YtDlpDownloadSizeExceeded,
    download_audio_via_ytdlp,
)


def test_download_audio_passes_explicit_size_limit_to_ytdlp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def _download(url: str, options: dict[str, object], *, deadline: float) -> Path:
        captured.update(url=url, options=options, deadline=deadline)
        output = tmp_path / "audio.webm"
        output.write_bytes(b"audio")
        return output

    monkeypatch.setattr(audio_pipeline, "_run_ytdlp_with_deadline", _download)

    result = download_audio_via_ytdlp("https://youtu.be/example", tmp_path)

    assert result == tmp_path / "audio.webm"
    assert captured["url"] == "https://youtu.be/example"
    options = captured["options"]
    assert isinstance(options, dict)
    assert options["max_filesize"] == audio_pipeline.MAX_YTDLP_AUDIO_BYTES


def test_progress_hook_enforces_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio_pipeline.time, "monotonic", lambda: 11.0)

    with pytest.raises(YtDlpDownloadDeadlineExceeded, match="deadline"):
        audio_pipeline._enforce_ytdlp_progress_limits({}, deadline=10.0)


@pytest.mark.parametrize(
    "field",
    ["downloaded_bytes", "total_bytes", "total_bytes_estimate"],
)
def test_progress_hook_enforces_reported_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    monkeypatch.setattr(audio_pipeline, "MAX_YTDLP_AUDIO_BYTES", 5)

    with pytest.raises(YtDlpDownloadSizeExceeded, match="byte limit"):
        audio_pipeline._enforce_ytdlp_progress_limits(
            {field: 6},
            deadline=audio_pipeline.time.monotonic() + 10,
        )


def test_progress_hook_enforces_partial_file_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(audio_pipeline, "MAX_YTDLP_AUDIO_BYTES", 5)
    partial = tmp_path / "audio.webm.part"
    partial.write_bytes(b"123456")

    with pytest.raises(YtDlpDownloadSizeExceeded, match="byte limit"):
        audio_pipeline._enforce_ytdlp_progress_limits(
            {"tmpfilename": str(partial), "downloaded_bytes": 1},
            deadline=audio_pipeline.time.monotonic() + 10,
        )


def test_parent_deadline_terminates_hung_ytdlp_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ReceiveConnection:
        def __init__(self) -> None:
            self.poll_timeouts: list[float] = []
            self.closed = False

        def poll(self, timeout: float) -> bool:
            self.poll_timeouts.append(timeout)
            return False

        def close(self) -> None:
            self.closed = True

    class _SendConnection:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _Process:
        pid = None
        exitcode = None

        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.alive = False
            self.terminated = False

        def start(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.terminated = True
            self.alive = False

        def kill(self) -> None:
            self.alive = False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    receive_connection = _ReceiveConnection()
    send_connection = _SendConnection()
    process: _Process | None = None

    class _ProcessContext:
        def Pipe(self, *, duplex: bool):  # noqa: N802, ANN202 - multiprocessing test double
            assert duplex is False
            return receive_connection, send_connection

        def Process(self, **kwargs: object) -> _Process:  # noqa: N802
            nonlocal process
            process = _Process(**kwargs)
            return process

    monkeypatch.setattr(
        audio_pipeline.multiprocessing,
        "get_context",
        lambda method: _ProcessContext() if method == "spawn" else None,
    )
    monkeypatch.setattr(audio_pipeline.time, "monotonic", lambda: 100.0)

    with pytest.raises(YtDlpDownloadDeadlineExceeded, match="deadline"):
        audio_pipeline._run_ytdlp_with_deadline(
            "https://youtu.be/example",
            {},
            deadline=110.0,
        )

    assert receive_connection.poll_timeouts == [10.0]
    assert receive_connection.closed is True
    assert send_connection.closed is True
    assert process is not None
    assert process.terminated is True
    assert process.kwargs["daemon"] is True


def test_process_start_failure_closes_both_pipe_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Connection:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _Process:
        pid = None

        def start(self) -> None:
            raise OSError("unable to spawn")

    connections = (_Connection(), _Connection())

    class _ProcessContext:
        def Pipe(self, *, duplex: bool):  # noqa: N802, ANN202 - multiprocessing test double
            assert duplex is False
            return connections

        def Process(self, **_kwargs: object) -> _Process:  # noqa: N802
            return _Process()

    monkeypatch.setattr(
        audio_pipeline.multiprocessing,
        "get_context",
        lambda _method: _ProcessContext(),
    )

    with pytest.raises(OSError, match="unable to spawn"):
        audio_pipeline._run_ytdlp_with_deadline("https://youtu.be/example", {}, deadline=1)

    assert all(connection.closed for connection in connections)


def test_stop_ytdlp_process_terminates_the_child_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[tuple[int, int]] = []

    class _Process:
        pid = 42

        def __init__(self) -> None:
            self.alive = True
            self.terminate_called = False
            self.kill_called = False

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.terminate_called = True

        def kill(self) -> None:
            self.kill_called = True

        def join(self, timeout: float | None = None) -> None:
            if timeout is None:
                self.alive = False

    process = _Process()
    monkeypatch.setattr(audio_pipeline.os, "getpgid", lambda _pid: 42)
    monkeypatch.setattr(
        audio_pipeline.os,
        "killpg",
        lambda process_group, sent_signal: signals.append((process_group, sent_signal)),
    )

    audio_pipeline._stop_ytdlp_process(process)  # type: ignore[arg-type]

    assert signals == [
        (42, audio_pipeline.signal.SIGTERM),
        (42, audio_pipeline.signal.SIGKILL),
    ]
    assert process.terminate_called is False
    assert process.kill_called is False


def test_oversized_cached_file_is_removed_without_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(audio_pipeline, "MAX_YTDLP_AUDIO_BYTES", 5)
    cached = tmp_path / "audio.webm"
    cached.write_bytes(b"123456")

    def _unexpected_download(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("oversized cache must fail before starting yt-dlp")

    monkeypatch.setattr(audio_pipeline, "_run_ytdlp_with_deadline", _unexpected_download)

    with pytest.raises(YtDlpDownloadSizeExceeded, match="Cached"):
        download_audio_via_ytdlp("https://youtu.be/example", tmp_path)

    assert cached.exists() is False


def test_stale_partial_cache_is_removed_before_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    partial = tmp_path / "audio.webm.part"
    metadata = tmp_path / "audio.webm.ytdl"
    partial.write_bytes(b"partial")
    metadata.write_bytes(b"metadata")
    output = tmp_path / "audio.webm"

    def _download(*_args: object, **_kwargs: object) -> Path:
        assert partial.exists() is False
        assert metadata.exists() is False
        output.write_bytes(b"audio")
        return output

    monkeypatch.setattr(audio_pipeline, "_run_ytdlp_with_deadline", _download)

    assert download_audio_via_ytdlp("https://youtu.be/example", tmp_path) == output


def test_post_download_byte_limit_removes_oversized_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(audio_pipeline, "MAX_YTDLP_AUDIO_BYTES", 5)
    output = tmp_path / "audio.webm"

    def _download(*_args: object, **_kwargs: object) -> Path:
        output.write_bytes(b"123456")
        return output

    monkeypatch.setattr(audio_pipeline, "_run_ytdlp_with_deadline", _download)

    with pytest.raises(YtDlpDownloadSizeExceeded, match="byte limit"):
        download_audio_via_ytdlp("https://youtu.be/example", tmp_path)

    assert output.exists() is False


def test_download_audio_spawned_child_can_fetch_direct_audio(tmp_path: Path) -> None:
    audio_bytes = b"ID3\x04\x00\x00\x00\x00\x00\x00test-audio"

    class _AudioHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(audio_bytes)))
            self.end_headers()
            self.wfile.write(audio_bytes)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _AudioHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/sample.mp3"
        result = download_audio_via_ytdlp(url, tmp_path)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)

    assert result.read_bytes() == audio_bytes
