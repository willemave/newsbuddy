"""Tests for content analyzer service."""

from unittest.mock import patch

import pytest

from app.models.metadata import ContentType
from app.services.content_analyzer import (
    ContentAnalysisOutput,
    ContentAnalysisResult,
    ContentAnalyzer,
    get_content_analyzer,
)
from app.services.url_detection import (
    infer_content_type_and_platform,
    should_use_llm_analysis,
)


class TestShouldUseLLMAnalysis:
    """Tests for should_use_llm_analysis function."""

    def test_skips_spotify(self):
        """Known Spotify URLs should skip LLM analysis."""
        assert not should_use_llm_analysis("https://open.spotify.com/episode/abc123")
        assert not should_use_llm_analysis("https://spotify.link/xyz")

    def test_skips_youtube(self):
        """Known YouTube URLs should skip LLM analysis."""
        assert not should_use_llm_analysis("https://youtube.com/watch?v=abc123")
        assert not should_use_llm_analysis("https://www.youtube.com/watch?v=abc123")
        assert not should_use_llm_analysis("https://youtu.be/abc123")
        assert not should_use_llm_analysis("https://m.youtube.com/watch?v=abc123")

    def test_skips_apple_podcasts(self):
        """Known Apple Podcast URLs should skip LLM analysis."""
        assert not should_use_llm_analysis("https://podcasts.apple.com/us/podcast/xyz")
        assert not should_use_llm_analysis("https://music.apple.com/us/album/xyz")

    def test_skips_overcast(self):
        """Known Overcast URLs should skip LLM analysis."""
        assert not should_use_llm_analysis("https://overcast.fm/+abc123")

    def test_uses_llm_for_unknown_urls(self):
        """Unknown URLs should use LLM analysis."""
        assert should_use_llm_analysis("https://example.com/some-podcast")
        assert should_use_llm_analysis("https://transistor.fm/episode/123")
        assert should_use_llm_analysis("https://medium.com/article")
        assert should_use_llm_analysis("https://substack.com/post/title")


class TestInferContentTypeAndPlatform:
    """Tests for infer_content_type_and_platform function."""

    def test_explicit_type_returned(self):
        """Explicit content type should be returned as-is."""
        content_type, platform = infer_content_type_and_platform(
            "https://unknown-site.com/something",
            provided_type=ContentType.ARTICLE,
            platform_hint="custom",
        )
        assert content_type == ContentType.ARTICLE
        assert platform == "custom"

    def test_spotify_detected_as_article_with_platform(self):
        """Spotify share URLs should be routed as article with spotify platform."""
        content_type, platform = infer_content_type_and_platform(
            "https://open.spotify.com/episode/abc123",
            provided_type=None,
            platform_hint=None,
        )
        assert content_type == ContentType.ARTICLE
        assert platform == "spotify"

    def test_apple_podcasts_detected_as_podcast(self):
        """Apple Podcasts URLs should remain podcast content."""
        content_type, platform = infer_content_type_and_platform(
            "https://podcasts.apple.com/us/podcast/xyz/id1592743188?i=1000745113618",
            provided_type=None,
            platform_hint=None,
        )
        assert content_type == ContentType.PODCAST
        assert platform == "apple_podcasts"

    def test_youtube_single_video_detected_as_podcast(self):
        """Single-video YouTube URLs should map to podcast/video flow."""
        content_type, platform = infer_content_type_and_platform(
            "https://www.youtube.com/watch?v=abc123",
            provided_type=None,
            platform_hint=None,
        )
        assert content_type == ContentType.PODCAST
        assert platform == "youtube"

    def test_youtube_channel_detected_as_article(self):
        """Non-video YouTube share URLs should stay in article flow."""
        content_type, platform = infer_content_type_and_platform(
            "https://www.youtube.com/@openai",
            provided_type=None,
            platform_hint=None,
        )
        assert content_type == ContentType.ARTICLE
        assert platform == "youtube"

    def test_path_keyword_detection(self):
        """URLs with podcast keywords in path should be detected as podcast."""
        content_type, platform = infer_content_type_and_platform(
            "https://unknown-site.com/podcast/episode/123",
            provided_type=None,
            platform_hint=None,
        )
        assert content_type == ContentType.PODCAST

    def test_unknown_url_defaults_to_article(self):
        """Unknown URLs without podcast keywords should default to article."""
        content_type, platform = infer_content_type_and_platform(
            "https://example.com/some-page",
            provided_type=None,
            platform_hint=None,
        )
        assert content_type == ContentType.ARTICLE


class TestContentAnalysisResult:
    """Tests for ContentAnalysisResult Pydantic model."""

    def test_article_result(self):
        """Test creating an article analysis result."""
        result = ContentAnalysisResult(
            content_type="article",
            original_url="https://example.com/article",
            title="Test Article",
            platform="medium",
        )
        assert result.content_type == "article"
        assert result.media_url is None
        assert result.confidence == 0.8  # default

    def test_podcast_result_with_media_url(self):
        """Test creating a podcast result with media URL."""
        result = ContentAnalysisResult(
            content_type="podcast",
            original_url="https://example.com/podcast/episode",
            media_url="https://cdn.example.com/audio.mp3",
            media_format="mp3",
            title="Test Podcast Episode",
            duration_seconds=3600,
            platform="transistor",
            confidence=0.95,
        )
        assert result.content_type == "podcast"
        assert result.media_url == "https://cdn.example.com/audio.mp3"
        assert result.media_format == "mp3"
        assert result.duration_seconds == 3600

    def test_video_result(self):
        """Test creating a video analysis result."""
        result = ContentAnalysisResult(
            content_type="video",
            original_url="https://vimeo.com/123456",
            media_url="https://player.vimeo.com/video/123456.mp4",
            media_format="mp4",
            platform="vimeo",
        )
        assert result.content_type == "video"
        assert result.media_format == "mp4"


class TestContentAnalyzer:
    """Tests for ContentAnalyzer class using Responses API."""

    @patch("app.services.content_analyzer.get_settings")
    def test_missing_api_key_raises_error(self, mock_settings):
        """Missing API key should raise ValueError."""
        mock_settings.return_value.openai_api_key = None
        analyzer = ContentAnalyzer()

        with pytest.raises(ValueError, match="OPENAI_API_KEY not configured"):
            analyzer._get_agent()

    @patch("app.services.content_analyzer._fetch_page_content")
    def test_analyze_url_with_spotify_link(self, mock_fetch):
        """URL with Spotify link is parsed as podcast from LLM output."""
        mock_fetch.return_value = (
            '<a href="https://open.spotify.com/episode/abc123">Listen</a>',
            "Test Episode Title\nSome content...",
        )

        analyzer = ContentAnalyzer()
        analyzer._agent = type(
            "MockAgent",
            (),
            {
                "run_sync": lambda self, _prompt: type(
                    "MockRunResult",
                    (),
                    {
                        "output": ContentAnalysisOutput(
                            analysis=ContentAnalysisResult(
                                content_type="podcast",
                                original_url="https://example.com/pod",
                                media_url=None,
                                media_format=None,
                                title="Test Episode",
                                description=None,
                                duration_seconds=1800,
                                platform="spotify",
                                confidence=0.9,
                            ),
                            instruction=None,
                        )
                    },
                )()
            },
        )()
        result = analyzer.analyze_url("https://example.com/pod")

        assert isinstance(result, ContentAnalysisOutput)
        assert result.analysis.content_type == "podcast"
        assert result.analysis.media_url is None
        assert result.analysis.platform == "spotify"

    @patch("app.services.content_analyzer._fetch_page_content")
    def test_analyze_url_fetch_failure_still_uses_llm(self, mock_fetch):
        """Failed page fetch still attempts LLM analysis."""
        mock_fetch.return_value = (None, None)

        analyzer = ContentAnalyzer()
        analyzer._agent = type(
            "MockAgent",
            (),
            {
                "run_sync": lambda self, _prompt: type(
                    "MockRunResult",
                    (),
                    {
                        "output": ContentAnalysisOutput(
                            analysis=ContentAnalysisResult(
                                content_type="article",
                                original_url="https://example.com/article",
                                media_url=None,
                                media_format=None,
                                title="Test Article",
                                description=None,
                                duration_seconds=None,
                                platform=None,
                                confidence=0.8,
                            ),
                            instruction=None,
                        )
                    },
                )()
            },
        )()
        result = analyzer.analyze_url("https://example.com/article")

        assert isinstance(result, ContentAnalysisOutput)
        assert result.analysis.content_type == "article"


class TestGetContentAnalyzer:
    """Tests for get_content_analyzer singleton."""

    def test_returns_singleton(self):
        """get_content_analyzer should return the same instance."""
        # Reset the global instance
        import app.services.content_analyzer as ca_module

        ca_module._content_analyzer = None

        analyzer1 = get_content_analyzer()
        analyzer2 = get_content_analyzer()

        assert analyzer1 is analyzer2
