"""Bounded E2B download bridge for untrusted remote media."""

from __future__ import annotations

import shlex
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse
from uuid import uuid4

from app.core.logging import get_logger
from app.services.agent_vm_runtime import AgentVmError, AgentVmSession
from app.services.agent_vm_sessions import (
    AGENT_VM_MAX_FILE_BYTES,
    create_agent_vm_session,
    evict_agent_vm_session,
)
from app.services.llm_tasks import build_llm_task_paths

MAX_SANDBOX_MEDIA_BYTES = 500_000_000
SANDBOX_MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 300.0
SANDBOX_MEDIA_TOTAL_TIMEOUT_SECONDS = 600.0
SANDBOX_MEDIA_CHUNK_BYTES = AGENT_VM_MAX_FILE_BYTES
logger = get_logger(__name__)


class SandboxMediaDownloadError(AgentVmError):
    """Raised when remote media cannot be copied safely through E2B."""


def download_remote_media_in_sandbox(
    url: str,
    destination: Path,
    *,
    user_id: int,
    execution_id: int,
) -> Path:
    """Download an HTTP media URL in E2B and copy bounded bytes to local scratch."""
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise SandboxMediaDownloadError("Remote media URL must use HTTP or HTTPS")

    paths = build_llm_task_paths(user_id=user_id, llm_task_id=execution_id)
    token = uuid4().hex
    remote_path = f"scratch/podcast-media-{token}.download"
    chunk_path = f"scratch/podcast-media-{token}.chunk"
    partial_path = destination.with_name(f".{destination.name}.{token}.partial")
    session: AgentVmSession | None = None
    remote_files_created = False

    try:
        session = create_agent_vm_session(
            user_id=user_id,
            llm_task_id=execution_id,
            vm_namespace=paths.vm_namespace,
            workspace_path=paths.workspace_path,
            shared_workspace_path=paths.shared_workspace_path,
            feature="podcast_media_download",
            deadline=monotonic() + SANDBOX_MEDIA_TOTAL_TIMEOUT_SECONDS,
        )
        if session.provider != "e2b":
            raise SandboxMediaDownloadError("Remote media downloads require the E2B provider")

        remote_files_created = True
        result = session.execute_bash(
            _download_command(url=url, remote_path=remote_path),
            timeout_seconds=SANDBOX_MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
        )
        if result.exit_code != 0:
            raise SandboxMediaDownloadError(
                f"Sandbox media download failed with curl exit {result.exit_code}"
            )
        remote_size = _parse_remote_size(result.stdout)

        destination.parent.mkdir(parents=True, exist_ok=True)
        with partial_path.open("wb") as output:
            for chunk_index, offset in enumerate(range(0, remote_size, SANDBOX_MEDIA_CHUNK_BYTES)):
                expected_size = min(
                    SANDBOX_MEDIA_CHUNK_BYTES,
                    remote_size - offset,
                )
                chunk_result = session.execute_bash(
                    shlex.join(
                        [
                            "dd",
                            f"if={remote_path}",
                            f"of={chunk_path}",
                            f"bs={SANDBOX_MEDIA_CHUNK_BYTES}",
                            f"skip={chunk_index}",
                            "count=1",
                            "status=none",
                        ]
                    ),
                    timeout_seconds=30,
                )
                if chunk_result.exit_code != 0:
                    raise SandboxMediaDownloadError("Unable to stage sandbox media bytes")
                chunk = session.read_file_bytes(chunk_path, max_bytes=expected_size)
                if len(chunk) != expected_size:
                    raise SandboxMediaDownloadError("Sandbox media transfer was incomplete")
                output.write(chunk)

        cleanup_result = session.execute_bash(
            _cleanup_command(remote_path, chunk_path),
            timeout_seconds=30,
        )
        if cleanup_result.exit_code != 0:
            raise SandboxMediaDownloadError("Unable to clean up sandbox media files")
        remote_files_created = False
        partial_path.replace(destination)
        return destination
    except SandboxMediaDownloadError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize the sandbox/file bridge boundary
        raise SandboxMediaDownloadError("Sandbox media transfer failed") from exc
    finally:
        partial_path.unlink(missing_ok=True)
        if session is not None:
            if remote_files_created and session.provider == "e2b":
                try:
                    cleanup_result = session.execute_bash(
                        _cleanup_command(remote_path, chunk_path),
                        timeout_seconds=30,
                    )
                    if cleanup_result.exit_code != 0:
                        raise SandboxMediaDownloadError(
                            "Sandbox media cleanup returned a non-zero exit code"
                        )
                except Exception:  # noqa: BLE001 - preserve the primary media failure
                    evict_agent_vm_session(session)
                    logger.warning("Unable to clean up failed sandbox media download")
            try:
                session.close()
            except Exception:  # noqa: BLE001 - downloaded bytes are already bounded and local
                logger.warning("Unable to close sandbox media download session")


def _download_command(*, url: str, remote_path: str) -> str:
    curl_command = shlex.join(
        [
            "curl",
            "--location",
            "--fail",
            "--silent",
            "--show-error",
            "--retry",
            "2",
            "--retry-delay",
            "1",
            "--retry-all-errors",
            "--connect-timeout",
            "30",
            "--max-time",
            str(int(SANDBOX_MEDIA_DOWNLOAD_TIMEOUT_SECONDS)),
            "--max-redirs",
            "10",
            "--max-filesize",
            str(MAX_SANDBOX_MEDIA_BYTES),
            "--proto",
            "=http,https",
            "--proto-redir",
            "=http,https",
            "--user-agent",
            "Mozilla/5.0 (compatible; NewsAggregator/1.0; Podcast Downloader)",
            "--output",
            remote_path,
            url,
        ]
    )
    quoted_path = shlex.quote(remote_path)
    return (
        f"mkdir -p scratch && {curl_command} && "
        f"size=$(wc -c < {quoted_path}) && "
        f'[ "$size" -gt 0 ] && [ "$size" -le {MAX_SANDBOX_MEDIA_BYTES} ] && '
        'printf "%s" "$size"'
    )


def _parse_remote_size(stdout: str) -> int:
    try:
        size = int(stdout.strip())
    except ValueError as exc:
        raise SandboxMediaDownloadError("Sandbox media download returned an invalid size") from exc
    if size <= 0 or size > MAX_SANDBOX_MEDIA_BYTES:
        raise SandboxMediaDownloadError("Sandbox media download exceeded its byte limit")
    return size


def _cleanup_command(remote_path: str, chunk_path: str) -> str:
    return shlex.join(["rm", "-f", remote_path, chunk_path])
