import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    HttpUrl,
    PostgresDsn,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.core.model_defaults import (
    CHEAP_MODEL_SPEC,
    GOOGLE_FLASH_LITE_PREVIEW_MODEL_NAME,
    IMAGE_GENERATION_MODEL_NAME,
    OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC,
    SMART_MODEL_SPEC,
)

DATA_ROOT = Path("/data")


def _resolve_env_file() -> Path:
    env_file = os.getenv("NEWSLY_ENV_FILE", ".env")
    return Path(env_file).expanduser()


# Load the selected env file into os.environ so libraries like openai/pydantic-ai can read it
load_dotenv(dotenv_path=_resolve_env_file(), override=True)


def _local_storage_fallback(field_name: str) -> Path:
    cwd = Path.cwd()
    data_dir = cwd / "data"
    fallbacks = {
        "media_base_dir": data_dir / "media",
        "logs_base_dir": cwd / "logs",
        "images_base_dir": data_dir / "images",
        "content_body_local_root": data_dir / "content_bodies",
        "podcast_scratch_dir": data_dir / "scratch",
        "personal_markdown_root": data_dir / "personal_markdown",
    }
    return fallbacks[field_name]


def _is_container_data_path(path: Path) -> bool:
    return path.is_absolute() and (path == DATA_ROOT or DATA_ROOT in path.parents)


def _closest_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _is_writable_or_creatable(path: Path) -> bool:
    if path.exists():
        return os.access(path, os.W_OK)
    return os.access(_closest_existing_parent(path.parent), os.W_OK)


def _normalize_storage_path(path: Path, field_name: str) -> Path:
    candidate = path.expanduser()
    if not _is_container_data_path(candidate):
        return candidate
    if _is_writable_or_creatable(candidate):
        return candidate
    return _local_storage_fallback(field_name)


def _default_images_base_dir() -> Path:
    return _normalize_storage_path(DATA_ROOT / "images", "images_base_dir")


class QueueSettingsView(BaseModel):
    """Grouped worker and queue settings for internal consumers and diagnostics."""

    max_workers: int
    worker_timeout_seconds: int
    checkout_timeout_minutes: int
    queue_backpressure_max_pending_content: int
    queue_backpressure_max_pending_process_news_item: int
    max_retry_attempts: int
    max_retries: int


class AuthSettingsView(BaseModel):
    """Grouped auth settings without exposing secrets."""

    jwt_algorithm: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int
    admin_session_expire_minutes: int
    apple_jwks_url: str
    apple_signin_audiences: list[str]
    jwt_secret_configured: bool
    admin_password_configured: bool


class StorageSettingsView(BaseModel):
    """Grouped storage settings without exposing storage credentials."""

    media_base_dir: Path
    logs_base_dir: Path
    images_base_dir: Path
    content_body_storage_provider: str
    content_body_local_root: Path
    content_body_storage_prefix: str
    content_body_storage_bucket_configured: bool
    content_body_storage_endpoint_configured: bool
    content_body_storage_region: str | None
    content_body_storage_access_key_configured: bool
    content_body_storage_secret_key_configured: bool
    content_body_storage_timeout_seconds: int
    podcast_scratch_dir: Path
    personal_markdown_enabled: bool
    personal_markdown_root: Path


class ProviderSettingsView(BaseModel):
    """Grouped provider settings with secret fields reduced to configured flags."""

    openai_api_key_configured: bool
    openrouter_api_key_configured: bool
    anthropic_api_key_configured: bool
    google_api_key_configured: bool
    google_cloud_project_configured: bool
    google_cloud_location: str
    image_generation_model: str
    image_generation_fallback_model: str | None
    infographic_generation_provider: str
    infographic_generation_model: str | None
    infographic_generation_fallback_model: str | None
    runware_api_key_configured: bool
    cerebras_api_key_configured: bool
    exa_api_key_configured: bool
    elevenlabs_api_key_configured: bool
    elevenlabs_tts_voice_id_configured: bool
    listen_notes_api_key_configured: bool
    spotify_client_id_configured: bool
    spotify_client_secret_configured: bool
    podcast_index_api_key_configured: bool
    podcast_index_api_secret_configured: bool
    firecrawl_api_key_configured: bool
    chat_sandbox_provider: str
    chat_sandbox_e2b_api_key_configured: bool
    learning_deck_model: str
    learning_sandbox_provider: str
    learning_sandbox_e2b_api_key_configured: bool


class DiscoverySettingsView(BaseModel):
    """Grouped discovery and news-list model settings."""

    discovery_model: str
    discovery_candidate_model: str
    discovery_itunes_country: str | None
    discovery_min_favorites: int
    discovery_max_favorites: int
    discovery_exa_results: int
    news_embedding_model: str
    news_embedding_device: str
    news_list_reranker_enabled: bool
    news_list_reranker_model: str
    news_list_reranker_device: str
    news_list_reranker_max_candidates: int
    news_list_reranker_batch_size: int
    news_list_reranker_similarity_threshold: float
    news_group_model: str
    news_header_model: str


class XIntegrationSettingsView(BaseModel):
    """Grouped X/Twitter integration settings without exposing secrets."""

    x_app_bearer_token_configured: bool
    x_client_id_configured: bool
    x_client_secret_configured: bool
    x_oauth_redirect_uri: str | None
    x_oauth_authorize_url: str
    x_oauth_token_url: str
    x_token_encryption_key_configured: bool
    x_bookmark_sync_enabled: bool
    x_sync_min_interval_minutes: int
    x_bookmark_sync_min_interval_minutes: int


class IntegrationSettingsView(BaseModel):
    """Grouped integration settings."""

    x: XIntegrationSettingsView


class ObservabilitySettingsView(BaseModel):
    """Grouped logging and tracing settings without exposing secrets."""

    environment: str
    public_base_url: str | None
    debug: bool
    log_level: str
    langfuse_enabled: bool
    langfuse_public_key_configured: bool
    langfuse_secret_key_configured: bool
    langfuse_host: str
    langfuse_sample_rate: float | None
    langfuse_include_content: bool
    langfuse_include_binary_content: bool
    langfuse_instrumentation_version: int


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",  # Ignore extra fields from existing .env
    )

    # Database
    database_url: PostgresDsn
    database_pool_size: int = 20
    database_max_overflow: int = 40

    # Application
    app_name: str = "News Aggregator"
    environment: str = "development"
    public_base_url: HttpUrl | None = None
    debug: bool = False
    log_level: str = "INFO"
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

    # Authentication settings
    JWT_SECRET_KEY: str = Field(..., description="Secret key for JWT token signing")
    JWT_ALGORITHM: str = Field(default="HS256", description="JWT signing algorithm")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=43200, description="Access token expiry in minutes (30 days)"
    )
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=90, description="Refresh token expiry in days")
    ADMIN_PASSWORD: str = Field(..., description="Admin password for web access")
    admin_session_expire_minutes: int = Field(default=10_080, ge=1)
    apple_jwks_url: str = "https://appleid.apple.com/auth/keys"
    apple_signin_audiences: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["org.willemaw.newsly"]
    )

    # Worker configuration
    max_workers: int = 1
    worker_timeout_seconds: int = 300
    # Claim-loop threads per worker process. One process per queue; concurrency
    # comes from threads inside it. Set every value to 1 to get the historical
    # sequential workers back.
    worker_threads_default: int = Field(default=4, ge=1, le=32)
    worker_threads_content: int = Field(default=6, ge=1, le=32)
    worker_threads_media: int = Field(default=3, ge=1, le=32)
    # Audio episodes already fan their chunks across an in-task TTS pool;
    # stacking claim threads on top would multiply concurrent ElevenLabs calls.
    worker_threads_audio_episode: int = Field(default=1, ge=1, le=32)
    checkout_timeout_minutes: int = 30
    queue_backpressure_max_pending_content: int = Field(default=150, ge=1)
    queue_backpressure_max_pending_process_news_item: int = Field(default=75, ge=1)

    # Content processing
    max_content_length: int = 100_000
    max_retry_attempts: int = 3
    max_retries: int = 3

    # News list ranking
    # Hosted by default: a local sentence-transformers model costs 1-2.5 GB of RSS in
    # every content worker. Any non-"openrouter:" value still loads locally.
    news_embedding_model: str = "openrouter:qwen/qwen3-embedding-8b"
    news_embedding_device: str = "auto"  # auto, cpu, cuda, mps (local models only)
    news_list_reranker_enabled: bool = False
    news_list_reranker_model: str = "Qwen/Qwen3-Reranker-4B"
    news_list_reranker_device: str = "auto"  # auto, cpu, cuda, mps
    news_list_reranker_max_candidates: int = Field(default=8, ge=1, le=32)
    news_list_reranker_batch_size: int = Field(default=4, ge=1, le=16)
    news_list_reranker_max_length: int = Field(default=2048, ge=256, le=8192)
    news_list_reranker_similarity_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    news_group_model: str = CHEAP_MODEL_SPEC
    news_header_model: str = CHEAP_MODEL_SPEC
    news_list_warm_embeddings: bool = True
    news_list_related_lookback_days: int = Field(default=7, ge=1, le=30)
    news_list_max_related_candidates: int = Field(default=150, ge=1)
    news_list_primary_similarity_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    news_list_secondary_similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)

    # Briefing tab
    briefing_enabled_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=lambda: [1])
    briefing_model: str = OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
    briefing_masthead_title: str = "The Unread Times"
    briefing_category_similarity: float = Field(default=0.55, ge=0.0, le=1.0)
    briefing_category_cluster_similarity: float = Field(default=0.62, ge=0.0, le=1.0)
    briefing_semantic_category_assignment_enabled: bool = True
    briefing_category_embedding_model: str = "openrouter:qwen/qwen3-embedding-8b"
    briefing_category_embedding_batch_size: int = Field(default=32, ge=1, le=128)
    briefing_category_embedding_timeout_seconds: int = Field(default=30, ge=1, le=120)
    briefing_new_lens_min_items: int = Field(default=3, ge=2, le=20)
    briefing_max_news_lenses: int = Field(default=10, ge=3, le=30)
    briefing_category_absorb_similarity: float = Field(default=0.45, ge=0.0, le=1.0)
    briefing_centroid_max_weight: int = Field(default=32, ge=4, le=500)
    briefing_taxonomy_planner_enabled: bool = True
    briefing_taxonomy_model: str | None = None
    briefing_taxonomy_sources_per_lens: int = Field(default=7, ge=1, le=12)
    briefing_taxonomy_llm_timeout_seconds: int = Field(default=300, ge=30, le=600)
    briefing_lens_idle_days: int = Field(default=7, ge=1, le=90)
    briefing_window_min: int = Field(default=3, ge=1, le=12)
    briefing_window_max: int = Field(default=6, ge=1, le=12)
    briefing_news_window_max: int = Field(default=4, ge=2, le=4)
    briefing_max_figures_deep: int = Field(default=12, ge=0, le=50)
    briefing_max_figures_news: int = Field(default=6, ge=0, le=50)
    briefing_llm_timeout_seconds: int = Field(default=300, ge=1, le=600)
    briefing_compose_parallelism: int = Field(default=12, ge=1, le=16)
    briefing_debounce_seconds: int = Field(default=900, ge=0, le=86_400)
    briefing_sweep_seconds: int = Field(default=3600, ge=60, le=86_400)
    briefing_pending_max_age_seconds: int = Field(default=1500, ge=60, le=86_400)
    briefing_dig_hourly_limit: int = Field(default=60, ge=0, le=1000)
    briefing_discussion_strip_enabled: bool = True
    briefing_discussion_overview_max_chars: int = Field(default=280, ge=80, le=900)

    # External services
    openai_api_key: str | None = None
    openrouter_api_key: str | None = None
    # Providers that return malformed structured output for OpenRouter models.
    # Alibaba's deepseek-v4-flash endpoint dumps all block content into `weight`.
    openrouter_ignored_providers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Alibaba"]
    )
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    google_cloud_project: str | None = None
    google_cloud_location: str = "global"
    image_generation_model: str = IMAGE_GENERATION_MODEL_NAME
    image_generation_fallback_model: str | None = None
    infographic_generation_provider: Literal["google", "runware"] = "google"
    infographic_generation_model: str | None = None
    infographic_generation_fallback_model: str | None = None
    runware_api_key: str | None = None
    cerebras_api_key: str | None = None
    exa_api_key: str | None = None
    elevenlabs_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ELEVENLABS_API_KEY", "ELEVENLABS"),
    )
    elevenlabs_tts_voice_id: str | None = "JBFqnCBsd6RMkjVDRZzb"
    elevenlabs_podcast_host_voice_id: str | None = None
    elevenlabs_podcast_guest_voice_id: str | None = None
    elevenlabs_narration_tts_model: str = "eleven_flash_v2_5"
    elevenlabs_narration_tts_output_format: str = "mp3_44100_128"
    elevenlabs_narration_tts_speed: float = Field(default=1.0, ge=0.7, le=1.2)
    elevenlabs_audio_episode_tts_max_workers: int = Field(default=4, ge=1, le=8)
    exa_search_request_cost_usd: float | None = Field(default=0.007, ge=0.0)
    exa_content_result_cost_usd: float | None = Field(default=0.001, ge=0.0)
    exa_summary_result_cost_usd: float | None = Field(default=0.001, ge=0.0)
    exa_search_included_results: int = Field(default=10, ge=0)

    # Langfuse tracing
    langfuse_enabled: bool = True
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_sample_rate: float | None = None
    langfuse_include_content: bool = True
    langfuse_include_binary_content: bool = False
    langfuse_instrumentation_version: Literal[2, 3, 4, 5] = 5

    # Feed discovery
    discovery_model: str = Field(
        default=SMART_MODEL_SPEC,
        description="LLM model spec for feed discovery planning",
    )
    discovery_candidate_model: str = Field(
        default=CHEAP_MODEL_SPEC,
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
    x_posts_read_cost_usd: float | None = Field(default=0.005, ge=0.0)
    x_users_read_cost_usd: float | None = Field(default=0.01, ge=0.0)
    # Bookmarks have no since_id cursor; frequent first-page reads rely on X's
    # same-day resource deduplication and stop at our persisted last-seen ID.
    x_sync_min_interval_minutes: int = Field(default=15, ge=1)
    x_bookmark_sync_min_interval_minutes: int = Field(default=15, ge=1)

    # PDF extraction (Gemini)
    pdf_gemini_model: str = Field(
        default=GOOGLE_FLASH_LITE_PREVIEW_MODEL_NAME,
        description="Gemini model name for PDF extraction",
    )

    # Whisper transcription settings
    whisper_model_size: str = "base"  # tiny, base, small, medium, large
    whisper_device: str = "auto"  # auto, cpu, cuda, mps
    tweet_video_enabled: bool = True

    # HTTP client
    http_timeout_seconds: int = 30
    http_max_retries: int = 3

    # Firecrawl fallback extraction
    firecrawl_api_key: str | None = None
    firecrawl_timeout_seconds: int = Field(default=45, ge=1, le=300)
    firecrawl_credit_cost_usd: float | None = Field(default=0.00083, ge=0.0)

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
    content_body_storage_provider: Literal["local", "s3_compatible"] = "local"
    content_body_local_root: Path = Field(
        default_factory=lambda: Path.cwd() / "data" / "content_bodies"
    )
    content_body_storage_prefix: str = "content"
    content_body_storage_bucket: str | None = None
    content_body_storage_endpoint: str | None = None
    content_body_storage_region: str | None = None
    content_body_storage_access_key: str | None = None
    content_body_storage_secret_key: str | None = None
    content_body_storage_timeout_seconds: int = Field(default=30, ge=1, le=300)
    podcast_scratch_dir: Path = Field(default_factory=lambda: Path.cwd() / "data" / "scratch")
    personal_markdown_enabled: bool = True
    personal_markdown_root: Path = Field(
        default_factory=lambda: Path.cwd() / "data" / "personal_markdown"
    )
    personal_markdown_max_slug_length: int = Field(default=80, ge=16, le=160)
    chat_sandbox_provider: Literal["disabled", "local", "e2b"] = "disabled"
    chat_sandbox_e2b_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CHAT_SANDBOX_E2B_API_KEY", "E2B_API_KEY"),
    )
    chat_sandbox_template: str | None = None
    chat_sandbox_timeout_seconds: int = Field(default=900, ge=60, le=86_400)
    chat_sandbox_allow_internet_access: bool = True
    chat_sandbox_library_root: str = "/workspace/personal_markdown"
    chat_sandbox_max_output_chars: int = Field(default=12_000, ge=1_000, le=100_000)
    learning_deck_model: str = OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
    learning_sandbox_provider: Literal["disabled", "e2b"] = "e2b"
    learning_sandbox_e2b_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LEARNING_SANDBOX_E2B_API_KEY", "E2B_API_KEY"),
    )
    learning_sandbox_template: str | None = None
    learning_sandbox_timeout_seconds: int = Field(default=1800, ge=60, le=86_400)
    learning_sandbox_allow_internet_access: bool = True
    learning_sandbox_workdir: str = "/tmp/newsly_learning_deck"
    learning_sandbox_max_output_chars: int = Field(default=20_000, ge=1_000, le=200_000)
    llm_task_model: str = OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
    llm_task_sandbox_provider: Literal["disabled", "local", "e2b"] = "e2b"
    llm_task_sandbox_e2b_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_TASK_SANDBOX_E2B_API_KEY", "E2B_API_KEY"),
    )
    llm_task_sandbox_template: str | None = None
    llm_task_sandbox_timeout_seconds: int = Field(default=1800, ge=60, le=86_400)
    llm_task_sandbox_allow_internet_access: bool = True
    llm_task_sandbox_root: str = "/tmp/newsly"
    llm_task_sandbox_max_output_chars: int = Field(default=20_000, ge=1_000, le=200_000)
    llm_task_tool_token_ttl_seconds: int = Field(default=7200, ge=60, le=86_400)
    learning_deck_signed_url_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    learning_deck_max_index_html_bytes: int = Field(default=2_000_000, ge=10_000)
    learning_deck_max_source_notes_bytes: int = Field(default=1_000_000, ge=1_000)
    learning_deck_max_asset_count: int = Field(default=40, ge=0, le=200)
    learning_deck_max_asset_bytes: int = Field(default=5_000_000, ge=1_000)

    # crawl4ai table extraction
    crawl4ai_enable_table_extraction: bool = False
    crawl4ai_table_provider: str | None = None
    crawl4ai_table_css_selector: str | None = None
    crawl4ai_table_enable_chunking: bool = True
    crawl4ai_table_chunk_token_threshold: int = 3000
    crawl4ai_table_min_rows_per_chunk: int = 10
    crawl4ai_table_max_parallel_chunks: int = 5
    crawl4ai_table_verbose: bool = False

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, v: str | PostgresDsn | None) -> str | PostgresDsn:
        if v is None or not str(v).strip():
            raise ValueError("DATABASE_URL must be set")
        raw_value = str(v).strip()
        try:
            url = make_url(raw_value)
        except Exception:
            return raw_value
        if url.drivername.startswith("sqlite"):
            raise ValueError(
                "SQLite has been deprecated as a Newsly runtime dialect. "
                "Configure DATABASE_URL with PostgreSQL."
            )
        if url.drivername != "postgres" and not url.drivername.startswith("postgresql"):
            raise ValueError("DATABASE_URL must use a PostgreSQL SQLAlchemy dialect")
        return raw_value

    @field_validator(
        "cors_allow_origins",
        "apple_signin_audiences",
        "openrouter_ignored_providers",
        mode="before",
    )
    @classmethod
    def parse_string_list(cls, v: str | list[str] | tuple[str, ...] | None) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError("Expected a JSON list")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return [item.strip() for item in v if item.strip()]

    @field_validator("briefing_enabled_user_ids", mode="before")
    @classmethod
    def parse_int_list(cls, v: str | list[int] | tuple[int, ...] | None) -> list[int]:
        if v is None:
            return []
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError("Expected a JSON list")
                return [int(item) for item in parsed]
            return [int(item.strip()) for item in stripped.split(",") if item.strip()]
        return [int(item) for item in v]

    @model_validator(mode="after")
    def validate_production_security_settings(self) -> "Settings":
        is_production = self.environment.lower() == "production"
        if is_production and "*" in self.cors_allow_origins:
            raise ValueError("CORS_ALLOW_ORIGINS must be explicit in production")
        public_base_url = self.public_base_url
        if public_base_url is not None and (
            public_base_url.username
            or public_base_url.password
            or public_base_url.query
            or public_base_url.fragment
            or public_base_url.path not in {"", "/"}
        ):
            raise ValueError("PUBLIC_BASE_URL must be an origin without credentials or a path")
        if is_production:
            if public_base_url is None:
                raise ValueError("PUBLIC_BASE_URL must be set in production")
            if public_base_url.scheme != "https":
                raise ValueError("PUBLIC_BASE_URL must use HTTPS in production")
        if not self.apple_signin_audiences:
            raise ValueError("APPLE_SIGNIN_AUDIENCES must include at least one audience")
        return self

    @field_validator("pdf_gemini_model")
    @classmethod
    def validate_pdf_gemini_model(cls, v: str) -> str:
        value = v.strip()
        if not value:
            raise ValueError("PDF_GEMINI_MODEL must be set")
        if not re.match(r"^gemini-[\w\.-]+$", value):
            raise ValueError("PDF_GEMINI_MODEL must start with 'gemini-'")
        return value

    @field_validator(
        "media_base_dir",
        "logs_base_dir",
        "images_base_dir",
        "content_body_local_root",
        "podcast_scratch_dir",
        "personal_markdown_root",
        mode="after",
    )
    @classmethod
    def normalize_container_storage_paths(cls, v: Path, info: ValidationInfo) -> Path:
        return _normalize_storage_path(v, info.field_name or "")

    def worker_thread_count(self, queue_name: str) -> int:
        """Return how many claim-loop threads a queue's worker process should run."""
        configured = getattr(self, f"worker_threads_{queue_name}", None)
        if isinstance(configured, int):
            return configured
        return self.worker_threads_default

    @property
    def queue(self) -> QueueSettingsView:
        return QueueSettingsView(
            max_workers=self.max_workers,
            worker_timeout_seconds=self.worker_timeout_seconds,
            checkout_timeout_minutes=self.checkout_timeout_minutes,
            queue_backpressure_max_pending_content=self.queue_backpressure_max_pending_content,
            queue_backpressure_max_pending_process_news_item=(
                self.queue_backpressure_max_pending_process_news_item
            ),
            max_retry_attempts=self.max_retry_attempts,
            max_retries=self.max_retries,
        )

    @property
    def auth(self) -> AuthSettingsView:
        return AuthSettingsView(
            jwt_algorithm=self.JWT_ALGORITHM,
            access_token_expire_minutes=self.ACCESS_TOKEN_EXPIRE_MINUTES,
            refresh_token_expire_days=self.REFRESH_TOKEN_EXPIRE_DAYS,
            admin_session_expire_minutes=self.admin_session_expire_minutes,
            apple_jwks_url=self.apple_jwks_url,
            apple_signin_audiences=self.apple_signin_audiences,
            jwt_secret_configured=bool(self.JWT_SECRET_KEY),
            admin_password_configured=bool(self.ADMIN_PASSWORD),
        )

    @property
    def storage(self) -> StorageSettingsView:
        return StorageSettingsView(
            media_base_dir=self.media_base_dir,
            logs_base_dir=self.logs_base_dir,
            images_base_dir=self.images_base_dir,
            content_body_storage_provider=self.content_body_storage_provider,
            content_body_local_root=self.content_body_local_root,
            content_body_storage_prefix=self.content_body_storage_prefix,
            content_body_storage_bucket_configured=bool(self.content_body_storage_bucket),
            content_body_storage_endpoint_configured=bool(self.content_body_storage_endpoint),
            content_body_storage_region=self.content_body_storage_region,
            content_body_storage_access_key_configured=bool(self.content_body_storage_access_key),
            content_body_storage_secret_key_configured=bool(self.content_body_storage_secret_key),
            content_body_storage_timeout_seconds=self.content_body_storage_timeout_seconds,
            podcast_scratch_dir=self.podcast_scratch_dir,
            personal_markdown_enabled=self.personal_markdown_enabled,
            personal_markdown_root=self.personal_markdown_root,
        )

    @property
    def providers(self) -> ProviderSettingsView:
        return ProviderSettingsView(
            openai_api_key_configured=bool(self.openai_api_key),
            openrouter_api_key_configured=bool(self.openrouter_api_key),
            anthropic_api_key_configured=bool(self.anthropic_api_key),
            google_api_key_configured=bool(self.google_api_key),
            google_cloud_project_configured=bool(self.google_cloud_project),
            google_cloud_location=self.google_cloud_location,
            image_generation_model=self.image_generation_model,
            image_generation_fallback_model=self.image_generation_fallback_model,
            infographic_generation_provider=self.infographic_generation_provider,
            infographic_generation_model=self.infographic_generation_model,
            infographic_generation_fallback_model=self.infographic_generation_fallback_model,
            runware_api_key_configured=bool(self.runware_api_key),
            cerebras_api_key_configured=bool(self.cerebras_api_key),
            exa_api_key_configured=bool(self.exa_api_key),
            elevenlabs_api_key_configured=bool(self.elevenlabs_api_key),
            elevenlabs_tts_voice_id_configured=bool(self.elevenlabs_tts_voice_id),
            listen_notes_api_key_configured=bool(self.listen_notes_api_key),
            spotify_client_id_configured=bool(self.spotify_client_id),
            spotify_client_secret_configured=bool(self.spotify_client_secret),
            podcast_index_api_key_configured=bool(self.podcast_index_api_key),
            podcast_index_api_secret_configured=bool(self.podcast_index_api_secret),
            firecrawl_api_key_configured=bool(self.firecrawl_api_key),
            chat_sandbox_provider=self.chat_sandbox_provider,
            chat_sandbox_e2b_api_key_configured=bool(self.chat_sandbox_e2b_api_key),
            learning_deck_model=self.learning_deck_model,
            learning_sandbox_provider=self.learning_sandbox_provider,
            learning_sandbox_e2b_api_key_configured=bool(self.learning_sandbox_e2b_api_key),
        )

    @property
    def discovery(self) -> DiscoverySettingsView:
        return DiscoverySettingsView(
            discovery_model=self.discovery_model,
            discovery_candidate_model=self.discovery_candidate_model,
            discovery_itunes_country=self.discovery_itunes_country,
            discovery_min_favorites=self.discovery_min_favorites,
            discovery_max_favorites=self.discovery_max_favorites,
            discovery_exa_results=self.discovery_exa_results,
            news_embedding_model=self.news_embedding_model,
            news_embedding_device=self.news_embedding_device,
            news_list_reranker_enabled=self.news_list_reranker_enabled,
            news_list_reranker_model=self.news_list_reranker_model,
            news_list_reranker_device=self.news_list_reranker_device,
            news_list_reranker_max_candidates=self.news_list_reranker_max_candidates,
            news_list_reranker_batch_size=self.news_list_reranker_batch_size,
            news_list_reranker_similarity_threshold=self.news_list_reranker_similarity_threshold,
            news_group_model=self.news_group_model,
            news_header_model=self.news_header_model,
        )

    @property
    def integrations(self) -> IntegrationSettingsView:
        return IntegrationSettingsView(
            x=XIntegrationSettingsView(
                x_app_bearer_token_configured=bool(self.x_app_bearer_token),
                x_client_id_configured=bool(self.x_client_id),
                x_client_secret_configured=bool(self.x_client_secret),
                x_oauth_redirect_uri=self.x_oauth_redirect_uri,
                x_oauth_authorize_url=self.x_oauth_authorize_url,
                x_oauth_token_url=self.x_oauth_token_url,
                x_token_encryption_key_configured=bool(self.x_token_encryption_key),
                x_bookmark_sync_enabled=self.x_bookmark_sync_enabled,
                x_sync_min_interval_minutes=self.x_sync_min_interval_minutes,
                x_bookmark_sync_min_interval_minutes=self.x_bookmark_sync_min_interval_minutes,
            )
        )

    @property
    def observability(self) -> ObservabilitySettingsView:
        return ObservabilitySettingsView(
            environment=self.environment,
            public_base_url=str(self.public_base_url) if self.public_base_url else None,
            debug=self.debug,
            log_level=self.log_level,
            langfuse_enabled=self.langfuse_enabled,
            langfuse_public_key_configured=bool(self.langfuse_public_key),
            langfuse_secret_key_configured=bool(self.langfuse_secret_key),
            langfuse_host=self.langfuse_host,
            langfuse_sample_rate=self.langfuse_sample_rate,
            langfuse_include_content=self.langfuse_include_content,
            langfuse_include_binary_content=self.langfuse_include_binary_content,
            langfuse_instrumentation_version=self.langfuse_instrumentation_version,
        )

    def redacted_diagnostics(self) -> dict[str, object]:
        """Return operator-safe grouped config diagnostics."""

        groups = {
            "queue": self.queue,
            "auth": self.auth,
            "storage": self.storage,
            "providers": self.providers,
            "discovery": self.discovery,
            "integrations": self.integrations,
            "observability": self.observability,
        }
        return {
            "environment": self.environment,
            "redacted": True,
            "groups": {name: view.model_dump(mode="json") for name, view in groups.items()},
        }

    @property
    def tweet_video_media_dir(self) -> Path:
        """Return the directory for temporary tweet video audio downloads."""

        return (self.storage.media_base_dir / "tweet_videos").resolve()

    @property
    def logs_dir(self) -> Path:
        """Return the root directory for all log files.

        Returns:
            Path: Absolute directory path for log storage.
        """

        return self.storage.logs_base_dir.resolve()

    @property
    def content_body_root_dir(self) -> Path:
        """Return the local filesystem root for canonical content body storage."""
        return self.storage.content_body_local_root.resolve()

    @property
    def podcast_scratch_root(self) -> Path:
        """Return the scratch directory used by podcast media workers."""
        return self.storage.podcast_scratch_dir.resolve()

    @property
    def personal_markdown_root_dir(self) -> Path:
        """Return the local filesystem root for the per-user markdown library."""
        return self.storage.personal_markdown_root.resolve()


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()  # type: ignore[call-arg]
