"""Shared configuration for onboarding workflows."""

from app.core.model_defaults import FAST_MODEL_SPEC
from app.services.feed_detection import FeedDetector
from app.services.prompt_library import load_prompt

ONBOARDING_PRIMARY_MODEL = FAST_MODEL_SPEC
PROFILE_MODEL = ONBOARDING_PRIMARY_MODEL
FAST_DISCOVER_MODEL = ONBOARDING_PRIMARY_MODEL
VOICE_PARSE_MODEL = ONBOARDING_PRIMARY_MODEL
AUDIO_PLAN_MODEL = ONBOARDING_PRIMARY_MODEL
DISCOVERY_FALLBACK_MODELS: tuple[str, ...] = ()
AUDIO_PLAN_FALLBACK_MODELS: tuple[str, ...] = ()

PROFILE_TIMEOUT_SECONDS = 8
FAST_DISCOVER_TIMEOUT_SECONDS = 12
VOICE_PARSE_TIMEOUT_SECONDS = 6
AUDIO_PLAN_TIMEOUT_SECONDS = 8
ENRICH_TIMEOUT_SECONDS = 25

FAST_DISCOVER_MAX_QUERIES = 6
PROFILE_EXA_RESULTS = 3
FAST_DISCOVER_EXA_RESULTS = 12
ENRICH_MAX_QUERIES = 10
ENRICH_EXA_RESULTS = 12
DISCOVERY_PROMPT_MAX_WEB_RESULTS = 200
DISCOVERY_PROMPT_SNIPPET_CHARS = 280
ONBOARDING_FEED_SUGGESTION_LIMIT = 5
EXA_DISCOVERY_MAX_WORKERS = 8

DEFAULT_SOURCE_LIMITS = {
    "substack": 8,
    "podcast_rss": 5,
    "atom": 6,
    "reddit": 5,
}
NEWS_SEED_LIMIT = 100
FEED_CONTENT_SEED_LIMIT = 30
FEED_SUGGESTION_TYPES = {"substack", "atom", "podcast_rss"}

SCRAPER_SOURCE_BY_TYPE = {
    "substack": "Substack",
    "podcast_rss": "Podcast",
    "atom": "Atom",
    "reddit": "Reddit",
}
ONBOARDING_FEED_DETECTOR = FeedDetector(use_llm=False, use_exa_search=False)

PROFILE_SYSTEM_PROMPT = load_prompt("onboarding/profile#system")
FAST_DISCOVER_SYSTEM_PROMPT = load_prompt("onboarding/fast_discover#system")
VOICE_PARSE_SYSTEM_PROMPT = load_prompt("onboarding/voice_parse#system")
AUDIO_PLAN_SYSTEM_PROMPT = load_prompt("onboarding/audio_plan#system")
