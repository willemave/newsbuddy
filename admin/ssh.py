"""SSH and rsync helpers for the operator CLI."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from admin.config import AdminConfig


class RemoteCommandError(RuntimeError):
    """Raised when a remote command fails."""

    def __init__(self, message: str, *, stderr: str | None = None) -> None:
        super().__init__(message)
        self.stderr = stderr


def run_remote_module(
    config: AdminConfig,
    *,
    action: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run `python -m admin.remote` on the remote host and parse its JSON result."""
    remote_command = (
        f"cd {shlex.quote(config.app_dir)} && "
        f"{shlex.quote(config.remote_python)} -m admin.remote {shlex.quote(action)}"
    )
    completed = subprocess.run(
        ["ssh", config.remote, remote_command],
        input=json.dumps(
            {
                "payload": payload or {},
                "context_override": _build_remote_context_override(config),
            }
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise RemoteCommandError(
            f"Remote command failed for action '{action}'",
            stderr=stderr or None,
        )
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RemoteCommandError(
            "Remote command returned invalid JSON",
            stderr=completed.stdout,
        ) from exc


def _build_remote_context_override(config: AdminConfig) -> dict[str, str] | None:
    """Return explicit remote paths when the CLI should avoid reading remote .env."""
    if config.remote_context_source != "direct":
        return None

    return {
        "database_url": f"sqlite:///{config.remote_db_path}",
        "logs_dir": config.logs_dir,
        "service_log_dir": config.service_log_dir,
    }


def run_remote_script(config: AdminConfig, script_args: list[str]) -> dict[str, Any]:
    """Run a trusted script inside the deployed app checkout."""
    quoted = " ".join(shlex.quote(part) for part in script_args)
    remote_command = (
        f"cd {shlex.quote(config.app_dir)} && "
        f"{shlex.quote(config.remote_python)} {quoted}"
    )
    completed = subprocess.run(
        ["ssh", config.remote, remote_command],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise RemoteCommandError("Remote script failed", stderr=stderr or None)
    return {
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "remote": config.remote,
        "command": script_args,
    }


def rsync_from_remote(config: AdminConfig, *, remote_path: str, local_path: Path) -> dict[str, Any]:
    """Sync a remote path to a local path with rsync."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["rsync", "-avz", f"{config.remote}:{remote_path}", str(local_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RemoteCommandError("rsync failed", stderr=completed.stderr.strip() or None)
    return {"stdout": completed.stdout, "destination": str(local_path)}
