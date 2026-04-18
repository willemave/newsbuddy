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


def _run_ssh_command(
    config: AdminConfig,
    remote_command: str,
    *,
    error_message: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["ssh", config.remote, remote_command],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise RemoteCommandError(error_message, stderr=stderr or None)
    return completed


def run_remote_module(
    config: AdminConfig,
    *,
    action: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run `python -m admin.remote` inside the Docker container and parse JSON."""
    remote_command = _build_docker_exec_command(
        config,
        [
            "python",
            "-m",
            "admin.remote",
            action,
        ],
    )
    request: dict[str, Any] = {
        "payload": payload or {},
        "context_override": _build_remote_context_override(config),
    }
    completed = _run_ssh_command(
        config,
        remote_command,
        error_message=f"Remote command failed for action '{action}'",
        input_text=json.dumps(request),
    )
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RemoteCommandError(
            "Remote command returned invalid JSON",
            stderr=completed.stdout,
        ) from exc


def _build_remote_context_override(config: AdminConfig) -> dict[str, str]:
    database_url = (
        config.remote_db_path
        if config.remote_context_source == "direct" and "://" in config.remote_db_path
        else _resolve_container_database_url(config)
    )
    return {
        "database_url": database_url,
        "logs_dir": config.logs_dir,
        "service_log_dir": config.service_log_dir,
    }


def _resolve_container_database_url(config: AdminConfig) -> str:
    remote_command = (
        f"cd {shlex.quote(config.app_dir)} && "
        f"sudo docker exec {shlex.quote(config.docker_service_name)} env"
    )
    completed = _run_ssh_command(
        config,
        remote_command,
        error_message="Remote env inspection failed",
    )

    env_values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_values[key] = value

    database_url = env_values.get("DATABASE_URL", "").strip()
    if database_url and "change-me" not in database_url:
        return database_url

    password = env_values.get("POSTGRES_PASSWORD", "").strip()
    user = env_values.get("POSTGRES_USER", "newsly").strip() or "newsly"
    database = env_values.get("POSTGRES_DB", "newsly").strip() or "newsly"
    port = env_values.get("POSTGRES_PORT", "5432").strip() or "5432"
    if password:
        return f"postgresql+psycopg://{user}:{password}@127.0.0.1:{port}/{database}"

    raise RemoteCommandError("Could not resolve remote database URL from container env")


def run_remote_script(config: AdminConfig, script_args: list[str]) -> dict[str, Any]:
    """Run a trusted script inside the Docker container."""
    runtime_env = _build_script_runtime_env(config)
    remote_command = _build_docker_exec_command(
        config,
        ["python", *script_args],
        env=runtime_env,
    )
    completed = _run_ssh_command(
        config,
        remote_command,
        error_message="Remote script failed",
    )
    return {
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "remote": config.remote,
        "command": script_args,
    }


def run_remote_docker_logs(
    config: AdminConfig,
    *,
    tail: int,
) -> dict[str, Any]:
    """Return recent logs from the unified Docker container stdout stream."""
    remote_command = (
        f"cd {shlex.quote(config.app_dir)} && "
        "sudo docker logs --timestamps "
        f"--tail {shlex.quote(str(tail))} {shlex.quote(config.docker_service_name)}"
    )
    completed = _run_ssh_command(
        config,
        remote_command,
        error_message="Remote docker logs failed",
    )
    return {
        "source": "docker",
        "remote": config.remote,
        "service": config.docker_service_name,
        "tail": tail,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _build_docker_exec_command_with_env(
    config: AdminConfig,
    quoted_command: str,
    *,
    env: dict[str, str] | None,
) -> str:
    docker_parts = ["sudo", "docker", "exec", "-i"]
    for key, value in (env or {}).items():
        docker_parts.extend(["-e", shlex.quote(f"{key}={value}")])
    docker_parts.append(shlex.quote(config.docker_service_name))
    docker_parts.append(quoted_command)
    return f"cd {shlex.quote(config.app_dir)} && {' '.join(docker_parts)}"


def _build_script_runtime_env(config: AdminConfig) -> dict[str, str]:
    try:
        database_url = _resolve_container_database_url(config)
    except RemoteCommandError:
        return {}
    return {
        "NEWSLY_ENV_FILE": "/tmp/empty.env",
        "DATABASE_URL": database_url,
    }


def _build_docker_exec_command(
    config: AdminConfig,
    command: list[str],
    *,
    env: dict[str, str] | None = None,
) -> str:
    quoted = " ".join(shlex.quote(part) for part in command)
    return _build_docker_exec_command_with_env(config, quoted, env=env)


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
