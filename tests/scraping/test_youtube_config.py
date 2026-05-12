from __future__ import annotations

import textwrap
from pathlib import Path

from app.scraping.youtube_config import (
    YouTubeChannelConfig,
    load_youtube_channels,
    load_youtube_client_config,
)


def write_config(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / "youtube.yml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def test_load_youtube_channels(tmp_path: Path) -> None:
    config = """
channels:
  - name: "Example"
    channel_id: "UC123"
    limit: 5
    max_age_days: 10
    language: "en"
"""

    config_path = write_config(tmp_path, config)

    channels = load_youtube_channels(config_path)

    assert len(channels) == 1
    channel = channels[0]
    assert channel.name == "Example"
    assert channel.channel_id == "UC123"
    assert channel.limit == 5
    assert channel.max_age_days == 10
    assert channel.language == "en"
    assert channel.target_url == "https://www.youtube.com/channel/UC123"


def test_channel_config_resolves_playlist_target() -> None:
    channel = YouTubeChannelConfig(name="Playlist", channel_id=None, playlist_id="PL12345")

    assert channel.target_url == "https://www.youtube.com/playlist?list=PL12345"


def test_missing_config_returns_empty(tmp_path: Path) -> None:
    config_path = tmp_path / "does-not-exist.yml"
    channels = load_youtube_channels(config_path)
    assert channels == []


def test_load_youtube_client_config(tmp_path: Path) -> None:
    config = textwrap.dedent(
        """
        client:
          cookies_path: "secrets/youtube_cookies.txt"
          po_token_provider: "bgutilhttp"
          po_token_base_url: "http://127.0.0.1:4416"
          throttle_seconds: 3
          player_client: "mweb"
        """
    )

    config_path = write_config(tmp_path, config)
    client_config = load_youtube_client_config(config_path)

    assert client_config.po_token_provider == "bgutilhttp"
    assert client_config.po_token_base_url == "http://127.0.0.1:4416"
    assert client_config.throttle_seconds == 3
    assert client_config.player_client == "mweb"
