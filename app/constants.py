"""Application-wide constants and defaults."""

from app.core.model_defaults import (
    CHEAP_GOOGLE_MODEL_SPEC,
    CHEAP_MODEL_SPEC,
    SMART_ANTHROPIC_MODEL_SPEC,
    SMART_MODEL_SPEC,
)

# Default LLM models
TWEET_SUGGESTION_MODEL = CHEAP_MODEL_SPEC

# LLM provider models for tweet suggestions
TWEET_MODELS = {
    "google": CHEAP_GOOGLE_MODEL_SPEC,
    "openai": SMART_MODEL_SPEC,
    "anthropic": SMART_ANTHROPIC_MODEL_SPEC,
}

# Worker type constants for checkout mechanism
WORKER_DOWNLOADER = "downloader"
WORKER_TRANSCRIBER = "transcriber"
WORKER_SUMMARIZER = "summarizer"

# Checkout timeout in minutes
DEFAULT_CHECKOUT_TIMEOUT_MINUTES = 30

# Pipeline polling interval in seconds
DEFAULT_POLLING_INTERVAL_SECONDS = 10

# Source label applied to user-submitted items
SELF_SUBMISSION_SOURCE = "self submission"

# Per-user content visibility/status values
CONTENT_STATUS_INBOX = "inbox"
CONTENT_STATUS_DIGEST_SOURCE = "digest_source"
CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY = "digest_only"

# Worker concurrency limits
DEFAULT_DOWNLOADER_CONCURRENCY = 5
DEFAULT_TRANSCRIBER_CONCURRENCY = 2
DEFAULT_SUMMARIZER_CONCURRENCY = 2

# Aggregate content platforms that should skip LLM summaries
AGGREGATE_PLATFORMS = {"twitter", "techmeme"}

# Default item limit for newly created feeds
DEFAULT_NEW_FEED_LIMIT = 1
DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT = 2

# Per-user aggregator subscriptions live in ``user_scraper_configs`` with this
# scraper_type and a ``feed_url`` of ``AGGREGATOR_FEED_URL_PREFIX + <key>``.
AGGREGATOR_SCRAPER_TYPE = "aggregator"
AGGREGATOR_FEED_URL_PREFIX = "aggregator://"

# Summary kind/version constants
SUMMARY_KIND_LONG_INTERLEAVED = "long_interleaved"
SUMMARY_KIND_LONG_STRUCTURED = "long_structured"
SUMMARY_KIND_LONG_BULLETS = "long_bullets"
SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE = "long_editorial_narrative"
SUMMARY_KIND_SHORT_NEWS = "short_news"
SUMMARY_KIND_DAILY_ROLLUP = "daily_rollup"
SUMMARY_KIND_LONGFORM_ARTIFACT = "longform_artifact"
SUMMARY_KIND_INSIGHT_REPORT = "insight_report"
SUMMARY_VERSION_V1 = 1
SUMMARY_VERSION_V2 = 2

DEFAULT_DAILY_DIGEST_SCHEDULER_LOOKBACK_HOURS = 6


# Worker ID format: {worker_type}_{instance_id}_{pid}
def generate_worker_id(worker_type: str, instance_id: str = "1") -> str:
    """Generate a unique worker ID for checkout mechanism."""
    import os

    pid = os.getpid()
    return f"{worker_type}_{instance_id}_{pid}"
