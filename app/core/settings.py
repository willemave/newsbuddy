import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env file into os.environ so libraries like openai/pydantic-ai can read it
load_dotenv(override=True)


def _default_images_base_dir() -> Path:
    data_root = Path("/data")
    images_root = data_root / "images"
    if data_root.exists():
        if images_root.exists() and os.access(images_root, os.W_OK):
            return images_root
        if os.access(data_root, os.W_OK):
            return images_root
    return Path.cwd() / "data" / "images"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",  # Ignore extra fields from existing .env
    )

    # Database - allow both PostgreSQL and SQLite for development
    database_url: PostgresDsn | str
    database_pool_size: int = 20
    database_max_overflow: int = 40

    # Application
    app_name: str = "News Aggregator"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"

    # Authentication settings
    JWT_SECRET_KEY: str = Field(..., description="Secret key for JWT token signing")
    JWT_ALGORITHM: str = Field(default="HS256", description="JWT signing algorithm")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=43200, description="Access token expiry in minutes (30 days)"
    )
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=90, description="Refresh token expiry in days")
    ADMIN_PASSWORD: str = Field(..., description="Admin password for web access")

    # Worker configuration
    max_workers: int = 1
    worker_timeout_seconds: int = 300
    checkout_timeout_minutes: int = 30

    # Content processing
    max_content_length: int = 100_000
    max_retry_attempts: int = 3
    max_retries: int = 3

    # News-native digest pipeline
    news_embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    news_embedding_device: str = "auto"  # auto, cpu, cuda, mps
    news_group_model: str = "google:gemini-3.1-flash-lite-preview"
    news_header_model: str = "google:gemini-3.1-flash-lite-preview"
    news_digest_primary_similarity_threshold: float = Field(default=0.86, ge=0.0, le=1.0)
    news_digest_secondary_similarity_threshold: float = Field(default=0.82, ge=0.0, le=1.0)
    news_digest_min_uncovered_items: int = Field(default=8, ge=1)
    news_digest_min_provisional_groups: int = Field(default=3, ge=1)
    news_digest_min_interval_minutes: int = Field(default=60, ge=1)
    news_digest_max_candidates: int = Field(default=150, ge=1)
    news_digest_scheduler_interval_minutes: int = Field(default=15, ge=1)
    news_digest_warm_embeddings: bool = True

    # External services
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    google_cloud_project: str | None = None
    google_cloud_location: str = "global"
    cerebras_api_key: str | None = None
    exa_api_key: str | None = None
    elevenlabs_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ELEVENLABS_API_KEY", "ELEVENLABS"),
    )
    elevenlabs_stt_model_id: str = "scribe_v2_realtime"
    elevenlabs_stt_language: str | None = None
    elevenlabs_tts_voice_id: str | None = "JBFqnCBsd6RMkjVDRZzb"
    elevenlabs_tts_model: str | None = "eleven_multilingual_v2"
    elevenlabs_tts_output_format: str | None = "pcm_16000"
    elevenlabs_digest_tts_model: str = "eleven_turbo_v2_5"
    elevenlabs_digest_tts_output_format: str = "mp3_44100_128"
    elevenlabs_digest_tts_speed: float = Field(default=1.0, ge=0.7, le=1.2)
    elevenlabs_agent_id: str = "agent_4701khf4v6jef3vskb8sd2a30m36"
    elevenlabs_agent_text_only: bool = True
    elevenlabs_agent_turn_timeout_seconds: int = 25
    voice_haiku_model: str = "google:gemini-3.1-flash-lite-preview"
    voice_session_ttl_minutes: int = 60
    voice_max_context_turns: int = 20
    voice_stt_commit_timeout_seconds: int = 8
    voice_max_input_seconds: int = 30
    voice_max_assistant_chars: int = 4_000
    voice_ws_max_queue: int = 500
    voice_trace_logging: bool = True
    voice_audio_diag_logging: bool = False
    voice_trace_max_chars: int = 600
    admin_conversational_session_ttl_minutes: int = 120
    admin_conversational_max_turns: int = 20
    admin_conversational_ws_max_queue: int = 500
    admin_conversational_trace_logging: bool = True
    admin_conversational_trace_max_chars: int = 1200

    # Langfuse tracing
    langfuse_enabled: bool = True
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_sample_rate: float | None = None
    langfuse_include_content: bool = True
    langfuse_include_binary_content: bool = False
    langfuse_instrumentation_version: Literal[1, 2, 3] = 2
    langfuse_event_mode: Literal["attributes", "logs"] = "attributes"

    # Feed discovery
    discovery_model: str = Field(
        default="anthropic:claude-opus-4-5-20251101",
        description="LLM model spec for feed discovery planning",
    )
    discovery_candidate_model: str = Field(
        default="google:gemini-3.1-flash-lite-preview",
        description="LLM model spec for discovery candidate extraction",
    )
    discovery_itunes_country: str | None = Field(
        default="us",
        description="Country code for iTunes lookup (e.g., us, au).",
    )
    discovery_min_favorites: int = Field(default=0, ge=0)
    discovery_max_favorites: int = Field(default=20, ge=5, le=50)
    discovery_exa_results: int = Field(default=8, ge=1, le=20)

    # Podcast online search
    listen_notes_api_key: str | None = None
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    spotify_market: str = "US"
    podcast_index_api_key: str | None = None
    podcast_index_api_secret: str | None = None
    podcast_index_user_agent: str = "newsly/1.0"
    podcast_search_cache_ttl_seconds: int = Field(default=300, ge=0, le=3600)
    podcast_search_provider_timeout_seconds: int = Field(default=6, ge=1, le=30)
    podcast_search_circuit_breaker_failures: int = Field(default=3, ge=1, le=10)
    podcast_search_circuit_breaker_cooldown_seconds: int = Field(default=120, ge=10, le=1800)

    # Twitter (tweet share scraping)
    twitter_auth_token: str | None = None
    twitter_ct0: str | None = None
    twitter_user_agent: str | None = None
    twitter_query_id_cache: Path | None = None

    # X API (official v2 + OAuth)
    x_app_bearer_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("X_APP_BEARER_TOKEN", "TWITTER_AUTH_TOKEN"),
    )
    x_client_id: str | None = None
    x_client_secret: str | None = None
    x_oauth_redirect_uri: str | None = None
    x_oauth_authorize_url: str = "https://x.com/i/oauth2/authorize"
    x_oauth_token_url: str = "https://api.x.com/2/oauth2/token"
    x_token_encryption_key: str | None = None
    x_bookmark_sync_enabled: bool = False

    # PDF extraction (Gemini)
    pdf_gemini_model: str = Field(
        default="gemini-3.1-flash-lite-preview",
        description="Gemini model name for PDF extraction",
    )

    # Whisper transcription settings
    whisper_model_size: str = "base"  # tiny, base, small, medium, large
    whisper_device: str = "auto"  # auto, cpu, cuda, mps

    # HTTP client
    http_timeout_seconds: int = 30
    http_max_retries: int = 3

    # Reddit / PRAW configuration (script flow)
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    reddit_username: str | None = None
    reddit_password: str | None = None
    reddit_read_only: bool = True
    reddit_user_agent: str | None = None

    # Storage paths
    media_base_dir: Path = Field(default_factory=lambda: Path.cwd() / "data" / "media")
    logs_base_dir: Path = Field(default_factory=lambda: Path.cwd() / "logs")
    images_base_dir: Path = Field(default_factory=_default_images_base_dir)

    # crawl4ai table extraction
    crawl4ai_enable_table_extraction: bool = False
    crawl4ai_table_provider: str | None = None
    crawl4ai_table_css_selector: str | None = None
    crawl4ai_table_enable_chunking: bool = True
    crawl4ai_table_chunk_token_threshold: int = 3000
    crawl4ai_table_min_rows_per_chunk: int = 10
    crawl4ai_table_max_parallel_chunks: int = 5
    crawl4ai_table_verbose: bool = False

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v):
        if not v:
            raise ValueError("DATABASE_URL must be set")
        # Allow SQLite for development
        if isinstance(v, str) and v.startswith("sqlite:"):
            return v
        return v

    @field_validator("pdf_gemini_model")
    @classmethod
    def validate_pdf_gemini_model(cls, v: str) -> str:
        value = v.strip()
        if not value:
            raise ValueError("PDF_GEMINI_MODEL must be set")
        if not re.match(r"^gemini-[\w\.-]+$", value):
            raise ValueError("PDF_GEMINI_MODEL must start with 'gemini-'")
        return value

    @property
    def podcast_media_dir(self) -> Path:
        """Return the directory for storing podcast media files.

        Returns:
            Path: Absolute directory path for podcast media output.
        """

        return (self.media_base_dir / "podcasts").resolve()

    @property
    def substack_media_dir(self) -> Path:
        """Return the directory for storing Substack assets.

        Returns:
            Path: Absolute directory path for Substack media output.
        """

        return (self.media_base_dir / "substack").resolve()

    @property
    def logs_dir(self) -> Path:
        """Return the root directory for all log files.

        Returns:
            Path: Absolute directory path for log storage.
        """

        return self.logs_base_dir.resolve()


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
