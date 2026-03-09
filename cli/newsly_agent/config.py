"""Config helpers for the remote Newsly agent CLI."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "newsly-agent"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"
CONFIG_PATH_ENV = "NEWSLY_AGENT_CONFIG_PATH"


@dataclass(frozen=True)
class AgentCliConfig:
    """User-local CLI configuration."""

    server_url: str | None = None
    api_key: str | None = None


def get_config_path(path: str | None = None) -> Path:
    """Resolve the effective config path."""
    if path:
        return Path(path).expanduser().resolve()
    env_path = os.environ.get(CONFIG_PATH_ENV)
    if env_path:
        return Path(env_path).expanduser().resolve()
    return DEFAULT_CONFIG_PATH


def load_config(path: str | None = None) -> AgentCliConfig:
    """Load CLI config from disk, returning defaults when absent."""
    config_path = get_config_path(path)
    if not config_path.exists():
        return AgentCliConfig()
    raw = config_path.read_text(encoding="utf-8").strip()
    if not raw:
        return AgentCliConfig()
    payload = json.loads(raw)
    return AgentCliConfig(
        server_url=payload.get("server_url"),
        api_key=payload.get("api_key"),
    )


def save_config(config: AgentCliConfig, path: str | None = None) -> Path:
    """Persist CLI config to disk."""
    config_path = get_config_path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(asdict(config), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with suppress(OSError):
        config_path.chmod(0o600)
    return config_path


def update_config(
    *,
    server_url: str | None = None,
    api_key: str | None = None,
    path: str | None = None,
) -> tuple[AgentCliConfig, Path]:
    """Merge updates into the existing config and write it back."""
    existing = load_config(path)
    updated = AgentCliConfig(
        server_url=server_url if server_url is not None else existing.server_url,
        api_key=api_key if api_key is not None else existing.api_key,
    )
    config_path = save_config(updated, path)
    return updated, config_path
