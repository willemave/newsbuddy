"""Tests for Newsly agent CLI config helpers."""

from __future__ import annotations

from cli.newsly_agent.config import AgentCliConfig, load_config, save_config, update_config


def test_config_round_trip(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    saved_path = save_config(
        AgentCliConfig(server_url="https://example.com", api_key="newsly_ak_test"),
        str(config_path),
    )

    loaded = load_config(str(saved_path))

    assert loaded.server_url == "https://example.com"
    assert loaded.api_key == "newsly_ak_test"


def test_update_config_preserves_existing_values(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    save_config(AgentCliConfig(server_url="https://example.com"), str(config_path))

    updated, _path = update_config(api_key="newsly_ak_test", path=str(config_path))

    assert updated.server_url == "https://example.com"
    assert updated.api_key == "newsly_ak_test"


def test_load_config_returns_defaults_for_empty_file(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("", encoding="utf-8")

    loaded = load_config(str(config_path))

    assert loaded.server_url is None
    assert loaded.api_key is None
