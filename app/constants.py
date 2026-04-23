"""Application-wide constants and defaults."""

# Default LLM models
TWEET_SUGGESTION_MODEL = "google-gla:gemini-3-pro-preview"

# LLM provider models for tweet suggestions
TWEET_MODELS = {
    "google": "google-gla:gemini-3-pro-preview",
    "openai": "openai:gpt-4o",
    "anthropic": "anthropic:claude-sonnet-4-5-20250929",
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

# Maximum number of representative news items kept visible in a user's fast-news
# feed. The pipeline still ingests/indexes everything; this just trims what the
# UI exposes to the most-recent N rows so the feed doesn't unbound over time.
NEWS_FEED_VISIBLE_LIMIT = 100

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
SUMMARY_VERSION_V1 = 1
SUMMARY_VERSION_V2 = 2

DEFAULT_DAILY_DIGEST_SCHEDULER_LOOKBACK_HOURS = 6


# Worker ID format: {worker_type}_{instance_id}_{pid}
def generate_worker_id(worker_type: str, instance_id: str = "1") -> str:
    """Generate a unique worker ID for checkout mechanism."""
    import os

    pid = os.getpid()
    return f"{worker_type}_{instance_id}_{pid}"
