"""Application-wide constants and defaults."""

# Default LLM models
TWEET_SUGGESTION_MODEL = "google-gla:gemini-3-pro-preview"

# Image generation model (Gemini with native image output)
IMAGE_GENERATION_MODEL = "google-gla:gemini-3-pro-image-preview"

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

# Worker concurrency limits
DEFAULT_DOWNLOADER_CONCURRENCY = 5
DEFAULT_TRANSCRIBER_CONCURRENCY = 2
DEFAULT_SUMMARIZER_CONCURRENCY = 2

# Aggregate content platforms that should skip LLM summaries
AGGREGATE_PLATFORMS = {"twitter", "techmeme"}

# Default item limit for newly created feeds
DEFAULT_NEW_FEED_LIMIT = 1

# Summary kind/version constants
SUMMARY_KIND_LONG_INTERLEAVED = "long_interleaved"
SUMMARY_KIND_LONG_STRUCTURED = "long_structured"
SUMMARY_KIND_LONG_BULLETS = "long_bullets"
SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE = "long_editorial_narrative"
SUMMARY_KIND_SHORT_NEWS_DIGEST = "short_news_digest"
SUMMARY_VERSION_V1 = 1
SUMMARY_VERSION_V2 = 2

# Daily digest synthesis model
DAILY_NEWS_DIGEST_MODEL = "google-gla:gemini-flash-latest"


# Worker ID format: {worker_type}_{instance_id}_{pid}
def generate_worker_id(worker_type: str, instance_id: str = "1") -> str:
    """Generate a unique worker ID for checkout mechanism."""
    import os

    pid = os.getpid()
    return f"{worker_type}_{instance_id}_{pid}"
