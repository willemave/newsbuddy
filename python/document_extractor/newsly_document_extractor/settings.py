"""Environment-only configuration for the extractor process.

This module deliberately does not read Newsly's env file or application settings. In particular,
there is no database URL, queue configuration, JWT secret, or durable storage path.
"""

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExtractorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCUMENT_EXTRACTOR_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    bind_host: str = "0.0.0.0"
    port: int = Field(default=8200, ge=1, le=65_535)
    shared_secret: SecretStr | None = None
    max_request_bytes: int = Field(default=32_768, ge=4_096, le=262_144)
    fetch_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    max_redirects: int = Field(default=8, ge=0, le=20)
    max_concurrent_extractions: int = Field(default=8, ge=1, le=64)
    crawler_max_crawls: int = Field(default=50, ge=1, le=500)
    crawler_max_age_seconds: int = Field(default=900, ge=60, le=7_200)
    table_extraction_provider: str | None = None
    table_extraction_api_token: SecretStr | None = None

    @model_validator(mode="after")
    def require_private_service_secret(self) -> "ExtractorSettings":
        if self.shared_secret is not None and not self.shared_secret.get_secret_value():
            raise ValueError("DOCUMENT_EXTRACTOR_SHARED_SECRET cannot be empty")
        if self.environment == "production" and self.shared_secret is None:
            raise ValueError("DOCUMENT_EXTRACTOR_SHARED_SECRET is required in production")
        if any(name in os.environ for name in ("DATABASE_URL", "NEWSLY_DATABASE_URL")):
            raise ValueError("Database configuration must not be present in the extractor process")
        return self


@lru_cache(maxsize=1)
def get_settings() -> ExtractorSettings:
    return ExtractorSettings()
