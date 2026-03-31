"""Configuration loading for the operator CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

DEFAULT_REMOTE = "willem@192.3.250.10"
DEFAULT_APP_DIR = "/opt/news_app"
DEFAULT_LOGS_DIR = "/data/logs"
DEFAULT_SERVICE_LOG_DIR = "/var/log/news_app"
DEFAULT_REMOTE_DB_PATH = "/data/news_app.db"
DEFAULT_REMOTE_PYTHON = ".venv/bin/python"
DEFAULT_REMOTE_CONTEXT_SOURCE = "direct"
DEFAULT_LOCAL_LOGS_DIR = "logs_from_server"
DEFAULT_LOCAL_DB_PATH = "admin/news_app.remote.db"
DEFAULT_PROMPT_REPORT_OUTPUT_DIR = "outputs"


@dataclass(frozen=True)
class AdminConfig:
    """Resolved runtime configuration for the operator CLI."""

    env_file: Path
    remote: str
    app_dir: str
    logs_dir: str
    service_log_dir: str
    remote_db_path: str
    remote_python: str
    remote_context_source: str
    local_logs_dir: Path
    local_db_path: Path
    prompt_report_output_dir: Path


def default_env_file() -> Path:
    """Return the default admin env-file location."""
    return Path.cwd() / "admin" / ".env"


def resolve_config(args: Any) -> AdminConfig:
    """Resolve CLI configuration from flags, env file, and defaults."""
    env_file = Path(getattr(args, "env_file", None) or default_env_file())
    env_values = _load_env_values(env_file)

    return AdminConfig(
        env_file=env_file,
        remote=_resolve_value(args, env_values, "remote", "ADMIN_REMOTE", DEFAULT_REMOTE),
        app_dir=_resolve_value(args, env_values, "app_dir", "ADMIN_APP_DIR", DEFAULT_APP_DIR),
        logs_dir=_resolve_value(args, env_values, "logs_dir", "ADMIN_LOGS_DIR", DEFAULT_LOGS_DIR),
        service_log_dir=_resolve_value(
            args,
            env_values,
            "service_log_dir",
            "ADMIN_SERVICE_LOG_DIR",
            DEFAULT_SERVICE_LOG_DIR,
        ),
        remote_db_path=_resolve_value(
            args,
            env_values,
            "remote_db_path",
            "ADMIN_REMOTE_DB_PATH",
            DEFAULT_REMOTE_DB_PATH,
        ),
        remote_python=_resolve_value(
            args,
            env_values,
            "remote_python",
            "ADMIN_REMOTE_PYTHON",
            DEFAULT_REMOTE_PYTHON,
        ),
        remote_context_source=_resolve_value(
            args,
            env_values,
            "remote_context_source",
            "ADMIN_REMOTE_CONTEXT_SOURCE",
            DEFAULT_REMOTE_CONTEXT_SOURCE,
        ),
        local_logs_dir=Path(
            _resolve_value(
                args,
                env_values,
                "local_logs_dir",
                "ADMIN_LOCAL_LOGS_DIR",
                DEFAULT_LOCAL_LOGS_DIR,
            )
        ),
        local_db_path=Path(
            _resolve_value(
                args,
                env_values,
                "local_db_path",
                "ADMIN_LOCAL_DB_PATH",
                DEFAULT_LOCAL_DB_PATH,
            )
        ),
        prompt_report_output_dir=Path(
            _resolve_value(
                args,
                env_values,
                "prompt_report_output_dir",
                "ADMIN_PROMPT_REPORT_OUTPUT_DIR",
                DEFAULT_PROMPT_REPORT_OUTPUT_DIR,
            )
        ),
    )


def _load_env_values(env_file: Path) -> dict[str, str]:
    if not env_file.exists():
        return {}
    loaded = dotenv_values(env_file)
    return {key: value for key, value in loaded.items() if value is not None}


def _resolve_value(
    args: Any,
    env_values: dict[str, str],
    attr_name: str,
    env_name: str,
    default: str,
) -> str:
    cli_value = getattr(args, attr_name, None)
    if cli_value not in (None, ""):
        return str(cli_value)
    return str(env_values.get(env_name, default))
