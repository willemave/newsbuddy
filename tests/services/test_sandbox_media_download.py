from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from app.services.agent_vm_runtime import AgentCommandResult
from app.services.sandbox_media_download import (
    SandboxMediaDownloadError,
    download_remote_media_in_sandbox,
)


class _FakeMediaSandbox:
    provider = "e2b"
    sandbox_id = "sandbox-media-test"

    def __init__(
        self,
        content: bytes,
        *,
        curl_exit_code: int = 0,
        cleanup_exit_code: int = 0,
    ) -> None:
        self.content = content
        self.curl_exit_code = curl_exit_code
        self.cleanup_exit_code = cleanup_exit_code
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []
        self.closed = False

    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AgentCommandResult:
        del timeout_seconds
        self.commands.append(command)
        if "curl" in command:
            curl_args = shlex.split(command.split("&&", 2)[1])
            remote_path = curl_args[curl_args.index("--output") + 1]
            if self.curl_exit_code != 0:
                return AgentCommandResult(
                    stdout="",
                    stderr="candidate download failed",
                    exit_code=self.curl_exit_code,
                )
            self.files[remote_path] = self.content
            return AgentCommandResult(stdout=str(len(self.content)), stderr="", exit_code=0)
        args = shlex.split(command)
        if args[0] == "dd":
            values = dict(argument.split("=", 1) for argument in args[1:])
            block_size = int(values["bs"])
            start = int(values["skip"]) * block_size
            self.files[values["of"]] = self.files[values["if"]][start : start + block_size]
        elif args[:2] == ["rm", "-f"]:
            if self.cleanup_exit_code != 0:
                return AgentCommandResult(
                    stdout="",
                    stderr="cleanup failed",
                    exit_code=self.cleanup_exit_code,
                )
            for path in args[2:]:
                self.files.pop(path, None)
        return AgentCommandResult(stdout="", stderr="", exit_code=0)

    def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        data = self.files[path]
        assert max_bytes is not None
        assert len(data) <= max_bytes
        return data

    def close(self) -> None:
        self.closed = True


def test_download_remote_media_keeps_link_local_fetch_in_e2b_and_copies_bytes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sandbox = _FakeMediaSandbox(b"podcast-bytes")
    calls: list[dict[str, object]] = []

    def _create_session(**kwargs):
        calls.append(kwargs)
        return sandbox

    monkeypatch.setattr(
        "app.services.sandbox_media_download.create_agent_vm_session",
        _create_session,
    )
    monkeypatch.setattr(
        "app.services.sandbox_media_download.SANDBOX_MEDIA_CHUNK_BYTES",
        4,
    )
    destination = tmp_path / "episode.mp3"
    url = "http://169.254.169.254/private-episode.mp3"

    result = download_remote_media_in_sandbox(
        url,
        destination,
        user_id=17,
        execution_id=42,
    )

    assert result == destination
    assert destination.read_bytes() == b"podcast-bytes"
    assert url in sandbox.commands[0]
    assert sum(command.startswith("dd ") for command in sandbox.commands) == 4
    assert sandbox.files == {}
    assert sandbox.closed is True
    assert calls[0]["feature"] == "podcast_media_download"
    assert calls[0]["vm_namespace"] == "user:17"


def test_download_remote_media_fails_closed_for_non_e2b_provider(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sandbox = _FakeMediaSandbox(b"podcast-bytes")
    sandbox.provider = "local"
    monkeypatch.setattr(
        "app.services.sandbox_media_download.create_agent_vm_session",
        lambda **_kwargs: sandbox,
    )

    with pytest.raises(SandboxMediaDownloadError, match="require the E2B"):
        download_remote_media_in_sandbox(
            "https://example.com/episode.mp3",
            tmp_path / "episode.mp3",
            user_id=17,
            execution_id=42,
        )

    assert sandbox.commands == []
    assert sandbox.closed is True


def test_download_remote_media_enforces_post_download_size_bound(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sandbox = _FakeMediaSandbox(b"123456")
    monkeypatch.setattr(
        "app.services.sandbox_media_download.create_agent_vm_session",
        lambda **_kwargs: sandbox,
    )
    monkeypatch.setattr(
        "app.services.sandbox_media_download.MAX_SANDBOX_MEDIA_BYTES",
        5,
    )

    with pytest.raises(SandboxMediaDownloadError, match="byte limit"):
        download_remote_media_in_sandbox(
            "https://example.com/episode.mp3",
            tmp_path / "episode.mp3",
            user_id=17,
            execution_id=42,
        )

    assert "--max-filesize 5" in sandbox.commands[0]
    assert sandbox.files == {}
    assert sandbox.closed is True


def test_download_remote_media_cleans_up_after_sandbox_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sandbox = _FakeMediaSandbox(b"", curl_exit_code=7)
    monkeypatch.setattr(
        "app.services.sandbox_media_download.create_agent_vm_session",
        lambda **_kwargs: sandbox,
    )
    destination = tmp_path / "episode.mp3"

    with pytest.raises(SandboxMediaDownloadError, match="curl exit 7"):
        download_remote_media_in_sandbox(
            "https://example.com/episode.mp3",
            destination,
            user_id=17,
            execution_id=42,
        )

    assert destination.exists() is False
    assert any(command.startswith("rm -f ") for command in sandbox.commands)
    assert sandbox.closed is True


def test_download_remote_media_evicts_session_when_failure_cleanup_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sandbox = _FakeMediaSandbox(b"", curl_exit_code=7, cleanup_exit_code=1)
    evicted: list[object] = []
    monkeypatch.setattr(
        "app.services.sandbox_media_download.create_agent_vm_session",
        lambda **_kwargs: sandbox,
    )
    monkeypatch.setattr(
        "app.services.sandbox_media_download.evict_agent_vm_session",
        evicted.append,
    )

    with pytest.raises(SandboxMediaDownloadError, match="curl exit 7"):
        download_remote_media_in_sandbox(
            "https://example.com/episode.mp3",
            tmp_path / "episode.mp3",
            user_id=17,
            execution_id=42,
        )

    assert evicted == [sandbox]
    assert sandbox.closed is True
