"""Tests for YouTube processing strategy."""

from datetime import datetime

import pytest
import yt_dlp

from app.processing_strategies.youtube_strategy import YouTubeProcessorStrategy
from app.scraping.youtube_unified import YouTubeClientConfig


@pytest.fixture(autouse=True)
def _mock_client_config_loader(mocker):
    """Keep strategy initialization deterministic during tests."""
    mocker.patch(
        "app.processing_strategies.youtube_strategy.load_youtube_client_config",
        return_value=YouTubeClientConfig(player_client="mweb"),
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://youtube.com/watch?v=abc", True),
        ("https://youtu.be/abc", True),
        ("https://youtube.com/shorts/abc", True),
        ("https://example.com/watch?v=abc", False),
    ],
)
def test_can_handle_url_patterns(mocker, url: str, expected: bool):
    """URL matching should accept only supported YouTube formats."""
    strategy = YouTubeProcessorStrategy(http_client=mocker.Mock())
    assert strategy.can_handle_url(url) is expected


def test_build_extractor_args_with_po_token_provider():
    """Extractor args should include provider-specific configuration when enabled."""
    cfg = YouTubeClientConfig(
        player_client="tv",
        po_token_provider="bgutilhttp",
        po_token_base_url="http://127.0.0.1:4416",
    )

    extractor_args = YouTubeProcessorStrategy._build_extractor_args(cfg)

    assert extractor_args["youtube"]["player_client"] == ["tv"]
    assert extractor_args["youtubepot-bgutilhttp"]["base_url"] == ["http://127.0.0.1:4416"]


def test_build_extractor_args_without_provider():
    """Extractor args should only include youtube key when provider is disabled."""
    cfg = YouTubeClientConfig(player_client="mweb")

    extractor_args = YouTubeProcessorStrategy._build_extractor_args(cfg)

    assert list(extractor_args.keys()) == ["youtube"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_message",
    [
        "Sign in to confirm you're not a bot",
        "Premieres in 5 minutes",
        "requires authentication",
    ],
)
async def test_extract_data_skips_known_download_errors(mocker, error_message):
    """Known authentication/premiere download errors should skip processing."""

    class DummyYDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, _url, **_kwargs):
            raise yt_dlp.utils.DownloadError(error_message)

    mocker.patch("app.processing_strategies.youtube_strategy.yt_dlp.YoutubeDL", DummyYDL)

    strategy = YouTubeProcessorStrategy(http_client=mocker.Mock())
    result = await strategy.extract_data(b"", "https://youtube.com/watch?v=abc")

    assert result["skip_processing"] is True
    assert "skip_reason" in result


@pytest.mark.asyncio
async def test_extract_data_raises_when_video_info_missing(mocker):
    """Unavailable/private videos should raise a clear ValueError."""

    class DummyYDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, _url, **_kwargs):
            return None

    mocker.patch("app.processing_strategies.youtube_strategy.yt_dlp.YoutubeDL", DummyYDL)

    strategy = YouTubeProcessorStrategy(http_client=mocker.Mock())

    with pytest.raises(ValueError, match="Failed to extract video information"):
        await strategy.extract_data(b"", "https://youtube.com/watch?v=abc")


@pytest.mark.asyncio
async def test_extract_data_falls_back_to_title_when_no_text_content(mocker):
    """When transcript and description are absent, title fallback should be used."""

    class DummyYDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, _url, **_kwargs):
            return {
                "id": "abc123",
                "title": "Sample Video",
                "uploader": "Test Channel",
                "description": "",
                "duration": 120,
                "upload_date": "20250101",
                "view_count": 1000,
                "like_count": 50,
                "thumbnail": "https://example.com/thumb.jpg",
            }

    mocker.patch("app.processing_strategies.youtube_strategy.yt_dlp.YoutubeDL", DummyYDL)
    mocker.patch(
        "app.processing_strategies.youtube_strategy.YouTubeProcessorStrategy._extract_transcript",
        return_value=None,
    )

    strategy = YouTubeProcessorStrategy(http_client=mocker.Mock())
    result = await strategy.extract_data(b"", "https://youtube.com/watch?v=abc")

    assert result["text_content"] == "YouTube Video: Sample Video"
    assert result["metadata"]["has_transcript"] is False
    assert result["content_type"] == "text"
    # Keep one concrete value check to ensure date parsing path still runs.
    assert datetime.fromisoformat(result["metadata"]["publication_date"]).year == 2025


@pytest.mark.asyncio
async def test_prepare_for_llm_includes_no_transcript_note(mocker):
    """LLM payload should include fallback note when transcript is unavailable."""
    strategy = YouTubeProcessorStrategy(http_client=mocker.Mock())

    prepared = await strategy.prepare_for_llm(
        {
            "title": "Video title",
            "metadata": {
                "channel": "Test Channel",
                "duration": 600,
                "view_count": 1234,
                "description": "Desc",
                "transcript": "",
            },
        }
    )

    assert "No transcript available" in prepared["content_to_summarize"]
    assert prepared["is_pdf"] is False
