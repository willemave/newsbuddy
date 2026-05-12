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

# Source label applied to user-submitted items
SELF_SUBMISSION_SOURCE = "self submission"

# Per-user content visibility/status values
CONTENT_STATUS_INBOX = "inbox"

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
SUMMARY_KIND_LONGFORM_ARTIFACT = "longform_artifact"
SUMMARY_VERSION_V1 = 1
SUMMARY_VERSION_V2 = 2
