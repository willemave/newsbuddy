"""Tweet-only GraphQL client and URL helpers for share-sheet ingestion."""

from __future__ import annotations

import json
import os
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)

TWITTER_API_BASE = "https://x.com/i/api/graphql"
TWITTER_BEARER_TOKEN = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAA7dbR1mQ4pcFZscR0gLDOk4ew3E"

DEFAULT_TWITTER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

TWEET_URL_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/(?:i/)?(?:status|[^/]+/status)/(\d+)",
    re.IGNORECASE,
)

DISCOVERY_PAGES = [
    "https://x.com/?lang=en",
    "https://x.com/explore",
    "https://x.com/notifications",
    "https://x.com/settings/profile",
]

BUNDLE_URL_REGEX = re.compile(
    r"https://abs\.twimg\.com/responsive-web/client-web(?:-legacy)?/[A-Za-z0-9.-]+\.js"
)
QUERY_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")

OPERATION_PATTERNS: list[dict[str, Any]] = [
    {
        "regex": re.compile(
            r"e\.exports=\{queryId\s*:\s*[\"']([^\"']+)[\"']\s*,\s*operationName\s*:\s*[\"']([^\"']+)[\"']",
            re.DOTALL,
        ),
        "operation_group": 2,
        "query_id_group": 1,
    },
    {
        "regex": re.compile(
            r"e\.exports=\{operationName\s*:\s*[\"']([^\"']+)[\"']\s*,\s*queryId\s*:\s*[\"']([^\"']+)[\"']",
            re.DOTALL,
        ),
        "operation_group": 1,
        "query_id_group": 2,
    },
    {
        "regex": re.compile(
            r"operationName\s*[:=]\s*[\"']([^\"']+)[\"'](.{0,4000}?)queryId\s*[:=]\s*[\"']([^\"']+)[\"']",
            re.DOTALL,
        ),
        "operation_group": 1,
        "query_id_group": 3,
    },
    {
        "regex": re.compile(
            r"queryId\s*[:=]\s*[\"']([^\"']+)[\"'](.{0,4000}?)operationName\s*[:=]\s*[\"']([^\"']+)[\"']",
            re.DOTALL,
        ),
        "operation_group": 3,
        "query_id_group": 1,
    },
]

QUERY_ID_TTL_SECONDS = 24 * 60 * 60
FALLBACK_QUERY_IDS = {
    "TweetDetail": "97JF30KziU00483E_8elBA",
}
EXTRA_TWEET_DETAIL_QUERY_IDS = ["aFvUsJm2c-oDkJV75blV6g"]


@dataclass(frozen=True)
class TwitterCredentials:
    """Auth cookies and user agent for Twitter GraphQL requests."""

    auth_token: str
    ct0: str
    user_agent: str


@dataclass(frozen=True)
class TweetExternalUrl:
    """External URL extracted from tweet entities."""

    expanded_url: str


@dataclass(frozen=True)
class TweetInfo:
    """Normalized tweet info needed for share ingestion."""

    id: str
    text: str
    author_username: str
    author_name: str
    created_at: str | None = None
    conversation_id: str | None = None
    like_count: int | None = None
    retweet_count: int | None = None
    reply_count: int | None = None
    external_urls: list[TweetExternalUrl] = field(default_factory=list)


@dataclass(frozen=True)
class TweetFetchParams:
    """Inputs for fetching tweet details."""

    tweet_id: str
    credentials: TwitterCredentials
    include_thread: bool = True
    timeout_seconds: float = 15.0
    query_id_cache_path: Path | None = None


@dataclass(frozen=True)
class TweetFetchResult:
    """Fetch result for tweet details."""

    success: bool
    tweet: TweetInfo | None = None
    thread: list[TweetInfo] = field(default_factory=list)
    external_urls: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class QueryIdSnapshot:
    """Cached snapshot of query IDs."""

    fetched_at: str
    ttl_seconds: int
    ids: dict[str, str]
    pages: list[str]
    bundles: list[str]


def extract_tweet_id(url: str) -> str | None:
    """Extract tweet ID from a tweet URL."""
    match = TWEET_URL_REGEX.search(url)
    return match.group(1) if match else None


def is_tweet_url(url: str) -> bool:
    """Return True if the URL appears to be a tweet."""
    return extract_tweet_id(url) is not None


def canonical_tweet_url(tweet_id: str) -> str:
    """Build a canonical tweet URL from ID."""
    return f"https://x.com/i/status/{tweet_id}"


def canonicalize_tweet_url(url_or_id: str) -> str | None:
    """Normalize a tweet URL (or ID) to https://x.com/i/status/<id>."""
    candidate = url_or_id.strip()
    if candidate.isdigit():
        return canonical_tweet_url(candidate)
    tweet_id = extract_tweet_id(candidate)
    return canonical_tweet_url(tweet_id) if tweet_id else None


def _resolve_query_id_cache_path(override: Path | None = None) -> Path:
    env_override = os.getenv("TWITTER_QUERY_ID_CACHE")
    settings = get_settings()
    if override:
        return override
    if settings.twitter_query_id_cache:
        return Path(settings.twitter_query_id_cache)
    if env_override:
        return Path(env_override).expanduser()
    return Path.cwd() / "data" / "twitter" / "query_ids_cache.json"


def _read_query_id_snapshot(path: Path) -> QueryIdSnapshot | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(raw, dict):
        return None

    fetched_at = raw.get("fetched_at")
    ttl_seconds = raw.get("ttl_seconds")
    ids = raw.get("ids")
    pages = raw.get("pages")
    bundles = raw.get("bundles")

    if (
        not isinstance(fetched_at, str)
        or not isinstance(ttl_seconds, int)
        or not isinstance(ids, dict)
        or not isinstance(pages, list)
        or not isinstance(bundles, list)
    ):
        return None

    normalized_ids = {
        key: value.strip()
        for key, value in ids.items()
        if isinstance(key, str) and isinstance(value, str) and value.strip()
    }

    return QueryIdSnapshot(
        fetched_at=fetched_at,
        ttl_seconds=ttl_seconds,
        ids=normalized_ids,
        pages=[p for p in pages if isinstance(p, str)],
        bundles=[b for b in bundles if isinstance(b, str)],
    )


def _write_query_id_snapshot(path: Path, snapshot: QueryIdSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": snapshot.fetched_at,
        "ttl_seconds": snapshot.ttl_seconds,
        "ids": snapshot.ids,
        "pages": snapshot.pages,
        "bundles": snapshot.bundles,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _snapshot_is_fresh(snapshot: QueryIdSnapshot) -> bool:
    try:
        fetched_at = datetime.fromisoformat(snapshot.fetched_at)
    except Exception:
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - fetched_at).total_seconds()
    return age <= snapshot.ttl_seconds


def _discover_bundle_urls(client: httpx.Client) -> list[str]:
    discovered: set[str] = set()
    headers = {"User-Agent": DEFAULT_TWITTER_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}

    for page_url in DISCOVERY_PAGES:
        try:
            response = client.get(page_url, headers=headers)
            response.raise_for_status()
            for match in BUNDLE_URL_REGEX.findall(response.text):
                discovered.add(match)
        except Exception:
            continue

    return list(discovered)


def _extract_query_ids(bundle_contents: str, targets: set[str], discovered: dict[str, str]) -> None:
    for pattern in OPERATION_PATTERNS:
        regex = pattern["regex"]
        for match in regex.finditer(bundle_contents):
            operation = match.group(pattern["operation_group"])
            query_id = match.group(pattern["query_id_group"])
            if operation not in targets or operation in discovered:
                continue
            if not QUERY_ID_REGEX.match(query_id):
                continue
            discovered[operation] = query_id
            if len(discovered) >= len(targets):
                return


def _refresh_query_ids(
    *,
    targets: set[str],
    cache_path: Path,
    timeout_seconds: float,
) -> dict[str, str]:
    headers = {"User-Agent": DEFAULT_TWITTER_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    discovered: dict[str, str] = {}
    bundles: list[str] = []

    with httpx.Client(timeout=timeout_seconds) as client:
        bundles = _discover_bundle_urls(client)
        for bundle_url in bundles:
            if len(discovered) >= len(targets):
                break
            try:
                response = client.get(bundle_url, headers=headers)
                response.raise_for_status()
                _extract_query_ids(response.text, targets, discovered)
            except Exception:
                continue

    if not discovered:
        return {}

    snapshot = QueryIdSnapshot(
        fetched_at=datetime.now(UTC).isoformat(),
        ttl_seconds=QUERY_ID_TTL_SECONDS,
        ids=discovered,
        pages=DISCOVERY_PAGES,
        bundles=bundles,
    )
    try:
        _write_query_id_snapshot(cache_path, snapshot)
    except Exception:
        logger.warning("Failed to write Twitter query ID cache to %s", cache_path)

    return discovered


def _get_query_ids(
    *,
    targets: set[str],
    timeout_seconds: float,
    cache_path: Path | None,
    force_refresh: bool = False,
) -> dict[str, str]:
    resolved_cache_path = _resolve_query_id_cache_path(cache_path)
    if not force_refresh:
        snapshot = _read_query_id_snapshot(resolved_cache_path)
        if snapshot and _snapshot_is_fresh(snapshot):
            return {k: v for k, v in snapshot.ids.items() if k in targets}

    return _refresh_query_ids(
        targets=targets,
        cache_path=resolved_cache_path,
        timeout_seconds=timeout_seconds,
    )


def _build_tweet_detail_features() -> dict[str, bool]:
    return {
        "rweb_video_screen_enabled": True,
        "profile_label_improvements_pcf_label_in_post_enabled": True,
        "responsive_web_profile_redirect_enabled": True,
        "rweb_tipjar_consumption_enabled": True,
        "verified_phone_label_enabled": False,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "premium_content_api_read_enabled": False,
        "communities_web_enable_tweet_community_results_fetch": True,
        "c9s_tweet_anatomy_moderator_badge_enabled": True,
        "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
        "responsive_web_grok_analyze_post_followups_enabled": False,
        "responsive_web_jetfuel_frame": True,
        "responsive_web_grok_share_attachment_enabled": True,
        "articles_preview_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "responsive_web_twitter_article_tweet_consumption_enabled": True,
        "tweet_awards_web_tipping_enabled": False,
        "responsive_web_grok_show_grok_translated_post": False,
        "responsive_web_grok_analysis_button_from_backend": True,
        "creator_subscriptions_quote_tweet_preview_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
        "rweb_video_timestamps_enabled": True,
        "longform_notetweets_rich_text_read_enabled": True,
        "longform_notetweets_inline_media_enabled": True,
        "responsive_web_grok_image_annotation_enabled": True,
        "responsive_web_grok_imagine_annotation_enabled": True,
        "responsive_web_grok_community_note_auto_translation_is_enabled": False,
        "responsive_web_twitter_article_plain_text_enabled": True,
        "responsive_web_twitter_article_seed_tweet_detail_enabled": True,
        "responsive_web_twitter_article_seed_tweet_summary_enabled": True,
        "responsive_web_enhance_cards_enabled": False,
    }


def _build_tweet_detail_variables(tweet_id: str) -> dict[str, Any]:
    return {
        "focalTweetId": tweet_id,
        "with_rux_injections": False,
        "rankingMode": "Relevance",
        "includePromotedContent": True,
        "withCommunity": True,
        "withQuickPromoteEligibilityTweetFields": True,
        "withBirdwatchNotes": True,
        "withVoice": True,
    }


def _cookie_header(auth_token: str, ct0: str) -> str:
    return f"auth_token={auth_token}; ct0={ct0}"


def _create_transaction_id() -> str:
    return secrets.token_hex(16)


def _build_headers(credentials: TwitterCredentials) -> dict[str, str]:
    return {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "authorization": TWITTER_BEARER_TOKEN,
        "x-csrf-token": credentials.ct0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "x-client-uuid": str(uuid.uuid4()),
        "x-twitter-client-deviceid": str(uuid.uuid4()),
        "x-client-transaction-id": _create_transaction_id(),
        "cookie": _cookie_header(credentials.auth_token, credentials.ct0),
        "user-agent": credentials.user_agent,
        "origin": "https://x.com",
        "referer": "https://x.com/",
    }


def _parse_response_json(response: httpx.Response) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = response.json()
    except Exception as exc:
        return None, f"Failed to parse JSON response: {exc}"
    if not isinstance(payload, dict):
        return None, "Unexpected JSON response shape"
    return payload, None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                return trimmed
    return None


def _collect_text_fields(value: Any, keys: set[str], output: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, list):
        for item in value:
            _collect_text_fields(item, keys, output)
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in keys and isinstance(nested, str):
                trimmed = nested.strip()
                if trimmed:
                    output.append(trimmed)
            _collect_text_fields(nested, keys, output)


def _extract_article_text(result: dict[str, Any] | None) -> str | None:
    if not result:
        return None
    article = result.get("article")
    if not isinstance(article, dict):
        return None

    article_result = article.get("article_results", {}).get("result", article)
    title = _first_text(article_result.get("title"), article.get("title"))
    body = _first_text(
        article_result.get("plain_text"),
        article.get("plain_text"),
        article_result.get("body", {}).get("text"),
        article_result.get("body", {}).get("richtext", {}).get("text"),
        article_result.get("body", {}).get("rich_text", {}).get("text"),
        article_result.get("content", {}).get("text"),
        article_result.get("content", {}).get("richtext", {}).get("text"),
        article_result.get("content", {}).get("rich_text", {}).get("text"),
        article_result.get("text"),
        article_result.get("richtext", {}).get("text"),
        article_result.get("rich_text", {}).get("text"),
        article.get("body", {}).get("text"),
        article.get("body", {}).get("richtext", {}).get("text"),
        article.get("body", {}).get("rich_text", {}).get("text"),
        article.get("content", {}).get("text"),
        article.get("content", {}).get("richtext", {}).get("text"),
        article.get("content", {}).get("rich_text", {}).get("text"),
        article.get("text"),
        article.get("richtext", {}).get("text"),
        article.get("rich_text", {}).get("text"),
    )

    if body and title and body.strip() == title.strip():
        body = None

    if not body:
        collected: list[str] = []
        _collect_text_fields(article_result, {"text", "title"}, collected)
        _collect_text_fields(article, {"text", "title"}, collected)
        unique = []
        seen: set[str] = set()
        for item in collected:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        if title:
            unique = [item for item in unique if item != title]
        if unique:
            body = "\n\n".join(unique)

    if title and body and not body.startswith(title):
        return f"{title}\n\n{body}"
    return body or title


def _extract_note_text(result: dict[str, Any] | None) -> str | None:
    note = (
        result
        if not result
        else result.get("note_tweet", {}).get("note_tweet_results", {}).get("result")
    )
    if not isinstance(note, dict):
        return None
    return _first_text(
        note.get("text"),
        note.get("richtext", {}).get("text"),
        note.get("rich_text", {}).get("text"),
        note.get("content", {}).get("text"),
        note.get("content", {}).get("richtext", {}).get("text"),
        note.get("content", {}).get("rich_text", {}).get("text"),
    )


def _extract_tweet_text(result: dict[str, Any] | None) -> str | None:
    if not result:
        return None
    return (
        _extract_article_text(result)
        or _extract_note_text(result)
        or _first_text(result.get("legacy", {}).get("full_text"))
    )


def _unwrap_tweet_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    current = result
    depth = 0
    while isinstance(current, dict) and depth < 6:
        if current.get("__typename") == "TweetTombstone":
            return None
        if isinstance(current.get("tweet"), dict):
            current = current["tweet"]
            depth += 1
            continue
        if isinstance(current.get("result"), dict):
            current = current["result"]
            depth += 1
            continue
        break
    return current if isinstance(current, dict) else None


def _normalize_tweet_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    current = _unwrap_tweet_result(result)
    if not current:
        return None

    legacy = current.get("legacy")
    if not isinstance(legacy, dict):
        if "full_text" in current or "text" in current:
            legacy = current
        else:
            return None

    return {
        "current": current,
        "legacy": legacy,
        "rest_id": current.get("rest_id") or legacy.get("id_str") or legacy.get("id"),
    }


def _extract_user_data(current: dict[str, Any], legacy: dict[str, Any]) -> dict[str, Any]:
    core = current.get("core", {}) if isinstance(current.get("core"), dict) else {}
    user_result = (
        core.get("user_results", {}).get("result")
        if isinstance(core.get("user_results"), dict)
        else None
    )
    user_data: dict[str, Any] = {}
    if isinstance(user_result, dict):
        normalized_user_result = user_result
        nested_user = user_result.get("user")
        if user_result.get("__typename") == "UserWithVisibilityResults" and isinstance(
            nested_user, dict
        ):
            normalized_user_result = nested_user
        legacy_user = normalized_user_result.get("legacy")
        user_data = legacy_user if isinstance(legacy_user, dict) else normalized_user_result

    if not user_data and isinstance(legacy.get("user"), dict):
        legacy_user = legacy.get("user")
        user_data = legacy_user if isinstance(legacy_user, dict) else {}

    if not user_data and isinstance(current.get("author"), dict):
        author_value = current.get("author")
        author = author_value if isinstance(author_value, dict) else {}
        legacy_author = author.get("legacy")
        result_author = author.get("result")
        user_data = (
            legacy_author
            if isinstance(legacy_author, dict)
            else result_author
            if isinstance(result_author, dict)
            else {}
        )

    return user_data if isinstance(user_data, dict) else {}


def _normalize_external_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if not parsed.netloc:
        return None

    domain = parsed.netloc.lower()
    if domain.endswith("twitter.com") or domain.endswith("x.com") or domain.endswith("t.co"):
        return None

    scheme = parsed.scheme or "https"
    normalized = parsed._replace(scheme=scheme).geturl()
    if normalized.startswith("http://"):
        normalized = "https://" + normalized[len("http://") :]
    return normalized


def _extract_external_urls(legacy: dict[str, Any]) -> list[TweetExternalUrl]:
    entities = legacy.get("entities", {}) if isinstance(legacy.get("entities"), dict) else {}
    urls = entities.get("urls", [])
    results: list[TweetExternalUrl] = []
    for url_info in urls:
        if not isinstance(url_info, dict):
            continue
        expanded = (
            url_info.get("expanded_url") or url_info.get("unwound_url") or url_info.get("url")
        )
        if not isinstance(expanded, str):
            continue
        normalized = _normalize_external_url(expanded)
        if not normalized:
            continue
        results.append(
            TweetExternalUrl(
                expanded_url=normalized,
            )
        )
    return results


def _map_tweet_result(result: dict[str, Any] | None) -> TweetInfo | None:
    normalized = _normalize_tweet_result(result)
    if not normalized:
        return None

    legacy = normalized["legacy"]
    current = normalized["current"]
    tweet_id = normalized["rest_id"]
    if not tweet_id:
        return None

    text = _extract_tweet_text(current)
    if not text:
        return None

    user_data = _extract_user_data(current, legacy)
    username = user_data.get("screen_name") or user_data.get("username") or "unknown"
    name = user_data.get("name") or username

    return TweetInfo(
        id=str(tweet_id),
        text=text,
        author_username=str(username),
        author_name=str(name),
        created_at=legacy.get("created_at"),
        conversation_id=legacy.get("conversation_id_str"),
        like_count=legacy.get("favorite_count"),
        retweet_count=legacy.get("retweet_count"),
        reply_count=legacy.get("reply_count"),
        external_urls=_extract_external_urls(legacy),
    )


def _collect_tweet_results_from_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def push(value: Any) -> None:
        if isinstance(value, dict) and value.get("rest_id"):
            results.append(value)

    def push_from(container: dict[str, Any], *keys: str) -> None:
        node: Any = container
        for key in keys:
            if not isinstance(node, dict):
                return
            node = node.get(key)
        push(node)

    content = entry.get("content", {}) if isinstance(entry.get("content"), dict) else {}
    push_from(content, "itemContent", "tweet_results", "result")
    push_from(content, "item", "itemContent", "tweet_results", "result")

    items = content.get("items", [])
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            push_from(item, "item", "itemContent", "tweet_results", "result")
            push_from(item, "itemContent", "tweet_results", "result")
            push_from(item, "content", "itemContent", "tweet_results", "result")

    return results


def _parse_tweets_from_instructions(instructions: list[dict[str, Any]] | None) -> list[TweetInfo]:
    tweets: list[TweetInfo] = []
    seen: set[str] = set()
    for instruction in instructions or []:
        entries = instruction.get("entries", [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for result in _collect_tweet_results_from_entry(entry):
                mapped = _map_tweet_result(result)
                if not mapped or mapped.id in seen:
                    continue
                seen.add(mapped.id)
                tweets.append(mapped)
    return tweets


def _find_tweet_in_instructions(
    instructions: list[dict[str, Any]] | None, tweet_id: str
) -> dict[str, Any] | None:
    for instruction in instructions or []:
        entries = instruction.get("entries", [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            result = (
                entry.get("content", {})
                .get("itemContent", {})
                .get("tweet_results", {})
                .get("result")
            )
            if isinstance(result, dict) and result.get("rest_id") == tweet_id:
                return result
    return None


def _sort_thread(tweets: list[TweetInfo]) -> list[TweetInfo]:
    def key(tweet: TweetInfo) -> tuple[int, str]:
        if tweet.created_at:
            try:
                dt = datetime.strptime(tweet.created_at, "%a %b %d %H:%M:%S %z %Y")
                return int(dt.timestamp()), tweet.id
            except Exception:
                pass
        return 0, tweet.id

    return sorted(tweets, key=key)


def _dedupe_external_urls(urls: list[TweetExternalUrl]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if url.expanded_url in seen:
            continue
        seen.add(url.expanded_url)
        result.append(url.expanded_url)
    return result


def fetch_tweet_detail(params: TweetFetchParams) -> TweetFetchResult:
    """Fetch tweet details (and optional thread) from Twitter GraphQL."""
    headers = _build_headers(params.credentials)
    variables = _build_tweet_detail_variables(params.tweet_id)
    features = _build_tweet_detail_features()

    query_ids = _get_query_ids(
        targets={"TweetDetail"},
        timeout_seconds=params.timeout_seconds,
        cache_path=params.query_id_cache_path,
        force_refresh=False,
    )
    primary_query_id = query_ids.get("TweetDetail") or FALLBACK_QUERY_IDS["TweetDetail"]
    query_id_candidates: list[str] = []
    seen_query_ids: set[str] = set()
    for candidate in [primary_query_id, *EXTRA_TWEET_DETAIL_QUERY_IDS]:
        if not candidate or candidate in seen_query_ids:
            continue
        seen_query_ids.add(candidate)
        query_id_candidates.append(candidate)

    def make_request(query_id: str) -> tuple[dict[str, Any] | None, str | None, int]:
        url = f"{TWITTER_API_BASE}/{query_id}/TweetDetail"
        params_payload = {
            "variables": json.dumps(variables),
            "features": json.dumps(features),
        }
        with httpx.Client(timeout=params.timeout_seconds) as client:
            response = client.get(url, headers=headers, params=params_payload)
            if response.status_code == 404:
                response = client.post(
                    url,
                    headers=headers,
                    json={"variables": variables, "features": features, "queryId": query_id},
                )
            payload, error = _parse_response_json(response)
            return payload, error, response.status_code

    last_error: str | None = None
    saw_404 = False
    payload: dict[str, Any] | None = None

    for query_id in query_id_candidates:
        payload, error, status_code = make_request(query_id)
        if status_code == 404:
            saw_404 = True
            last_error = error or "HTTP 404"
            continue
        if error:
            last_error = error
            continue
        if not payload:
            last_error = "Empty TweetDetail payload"
            continue
        if payload.get("errors"):
            last_error = ", ".join(
                str(err.get("message"))
                for err in payload.get("errors", [])
                if isinstance(err, dict)
            )
            continue
        break

    if (not payload or (payload and payload.get("errors"))) and saw_404:
        refreshed = _get_query_ids(
            targets={"TweetDetail"},
            timeout_seconds=params.timeout_seconds,
            cache_path=params.query_id_cache_path,
            force_refresh=True,
        )
        refreshed_id = refreshed.get("TweetDetail")
        if refreshed_id and refreshed_id not in query_id_candidates:
            payload, error, _status = make_request(refreshed_id)
            last_error = None if payload and not payload.get("errors") else error or last_error

    if not payload or payload.get("errors"):
        return TweetFetchResult(success=False, error=last_error or "TweetDetail request failed")

    data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    tweet_result = (
        data.get("tweetResult", {}).get("result")
        if isinstance(data.get("tweetResult"), dict)
        else None
    )
    instructions = (
        data.get("threaded_conversation_with_injections_v2", {}).get("instructions")
        if isinstance(data.get("threaded_conversation_with_injections_v2"), dict)
        else None
    )

    if not tweet_result and instructions:
        tweet_result = _find_tweet_in_instructions(instructions, params.tweet_id)

    tweet_info = _map_tweet_result(tweet_result)
    if not tweet_info:
        return TweetFetchResult(success=False, error="Tweet not found in response")

    thread_tweets = []
    if params.include_thread and instructions:
        parsed_tweets = _parse_tweets_from_instructions(instructions)
        target = next((tweet for tweet in parsed_tweets if tweet.id == tweet_info.id), None)
        root_id = target.conversation_id if target else tweet_info.conversation_id
        root_id = root_id or tweet_info.id
        thread_tweets = [tweet for tweet in parsed_tweets if tweet.conversation_id == root_id]
        if tweet_info.id not in {tweet.id for tweet in thread_tweets}:
            thread_tweets.append(tweet_info)
        thread_tweets = _sort_thread(thread_tweets)

    external_urls = _dedupe_external_urls(
        [url for tweet in ([tweet_info] + thread_tweets) for url in tweet.external_urls]
    )

    return TweetFetchResult(
        success=True,
        tweet=tweet_info,
        thread=thread_tweets,
        external_urls=external_urls,
    )
