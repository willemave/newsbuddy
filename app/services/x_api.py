"""Official X API v2 helpers for OAuth, tweets, bookmarks, timelines, and lists."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.twitter_share import extract_tweet_id

logger = get_logger(__name__)

X_API_BASE = "https://api.x.com/2"
X_DEFAULT_SCOPES = [
    "tweet.read",
    "users.read",
    "bookmark.read",
    "offline.access",
]
X_TWEET_FIELDS = (
    "created_at,author_id,public_metrics,entities,conversation_id,"
    "in_reply_to_user_id,referenced_tweets,text,article,note_tweet"
)
X_USER_FIELDS = "name,username"
X_TWEET_EXPANSIONS = "author_id,referenced_tweets.id,referenced_tweets.id.author_id"


@dataclass(frozen=True)
class XUser:
    """Normalized X user profile."""

    id: str
    username: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class XTweet:
    """Normalized X tweet payload."""

    id: str
    text: str
    author_id: str | None = None
    author_username: str | None = None
    author_name: str | None = None
    created_at: str | None = None
    like_count: int | None = None
    retweet_count: int | None = None
    reply_count: int | None = None
    conversation_id: str | None = None
    in_reply_to_user_id: str | None = None
    referenced_tweet_types: list[str] = field(default_factory=list)
    article_title: str | None = None
    article_text: str | None = None
    note_tweet_text: str | None = None
    external_urls: list[str] = field(default_factory=list)
    linked_tweet_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class XTokenResponse:
    """OAuth token response payload."""

    access_token: str
    refresh_token: str | None
    expires_in: int | None
    scopes: list[str]


@dataclass(frozen=True)
class XTweetFetchResult:
    """Fetch result for a tweet lookup call."""

    success: bool
    tweet: XTweet | None = None
    error: str | None = None


@dataclass(frozen=True)
class XTweetsPage:
    """Page of tweets returned from an X API collection endpoint."""

    tweets: list[XTweet]
    next_token: str | None = None


@dataclass(frozen=True)
class XList:
    """Minimal X list payload used for sync."""

    id: str
    name: str


@dataclass(frozen=True)
class XListsPage:
    """Page of X lists returned from the API."""

    lists: list[XList]
    next_token: str | None = None


def is_tweet_url(url: str) -> bool:
    """Return True when a URL is an X/Twitter status URL."""
    return extract_tweet_id(url) is not None


def build_oauth_authorize_url(*, state: str, code_challenge: str, scopes: list[str]) -> str:
    """Build the X OAuth authorize URL."""
    settings = get_settings()
    if not settings.x_client_id or not settings.x_oauth_redirect_uri:
        raise ValueError(
            "X OAuth is not configured (X_CLIENT_ID and X_OAUTH_REDIRECT_URI are required)"
        )
    params = {
        "response_type": "code",
        "client_id": settings.x_client_id,
        "redirect_uri": settings.x_oauth_redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{settings.x_oauth_authorize_url}?{urlencode(params)}"


def exchange_oauth_code(*, code: str, code_verifier: str) -> XTokenResponse:
    """Exchange an OAuth code for access/refresh tokens."""
    if not code.strip():
        raise ValueError("OAuth code is required")
    if not code_verifier.strip():
        raise ValueError("OAuth code verifier is required")
    payload = _oauth_token_request(
        grant_type="authorization_code",
        extra={
            "code": code.strip(),
            "code_verifier": code_verifier.strip(),
        },
    )
    return _parse_token_payload(payload)


def refresh_oauth_token(*, refresh_token: str) -> XTokenResponse:
    """Refresh an X OAuth access token."""
    if not refresh_token.strip():
        raise ValueError("Refresh token is required")
    payload = _oauth_token_request(
        grant_type="refresh_token",
        extra={"refresh_token": refresh_token.strip()},
    )
    return _parse_token_payload(payload)


def get_authenticated_user(*, access_token: str) -> XUser:
    """Fetch the currently authenticated X user."""
    payload = _request_json(
        "GET",
        f"{X_API_BASE}/users/me",
        access_token=access_token,
        params={"user.fields": X_USER_FIELDS},
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected X /users/me response payload")
    user_id = str(data.get("id") or "").strip()
    if not user_id:
        raise RuntimeError("X /users/me response missing user id")
    return XUser(
        id=user_id,
        username=_optional_string(data.get("username")),
        name=_optional_string(data.get("name")),
    )


def get_user_by_username(*, username: str, access_token: str | None = None) -> XUser | None:
    """Resolve a public X profile by username."""
    cleaned = username.strip().lstrip("@")
    if not cleaned:
        raise ValueError("Username is required")

    payload = _request_json(
        "GET",
        f"{X_API_BASE}/users/by/username/{cleaned}",
        access_token=access_token,
        allow_app_bearer=True,
        params={"user.fields": X_USER_FIELDS},
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    user_id = str(data.get("id") or "").strip()
    if not user_id:
        return None
    return XUser(
        id=user_id,
        username=_optional_string(data.get("username")),
        name=_optional_string(data.get("name")),
    )


def fetch_tweet_by_id(
    *,
    tweet_id: str,
    access_token: str | None = None,
) -> XTweetFetchResult:
    """Fetch a single tweet via X API v2."""
    cleaned = tweet_id.strip()
    if not cleaned.isdigit():
        return XTweetFetchResult(success=False, error="Invalid tweet id")

    try:
        payload = _request_json(
            "GET",
            f"{X_API_BASE}/tweets/{cleaned}",
            access_token=access_token,
            allow_app_bearer=True,
            params={
                "expansions": X_TWEET_EXPANSIONS,
                "tweet.fields": X_TWEET_FIELDS,
                "user.fields": X_USER_FIELDS,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return XTweetFetchResult(success=False, error=str(exc))

    data = payload.get("data")
    if not isinstance(data, dict):
        return XTweetFetchResult(success=False, error="Tweet not found")
    includes = payload.get("includes") if isinstance(payload.get("includes"), dict) else {}
    users = includes.get("users") if isinstance(includes, dict) else []
    lookup = _user_lookup(users)
    tweet = _map_tweet(data, lookup)
    if tweet is None:
        return XTweetFetchResult(success=False, error="Unable to parse tweet payload")
    return XTweetFetchResult(success=True, tweet=tweet)


def fetch_tweet_by_url(*, url: str, access_token: str | None = None) -> XTweetFetchResult:
    """Fetch a tweet from an X/Twitter status URL."""
    tweet_id = extract_tweet_id(url)
    if not tweet_id:
        return XTweetFetchResult(success=False, error="Invalid tweet URL")
    return fetch_tweet_by_id(tweet_id=tweet_id, access_token=access_token)


def fetch_tweets_by_ids(
    *,
    tweet_ids: list[str],
    access_token: str | None = None,
) -> list[XTweet]:
    """Fetch multiple tweets by id while preserving request order."""
    cleaned_ids: list[str] = []
    seen: set[str] = set()
    for tweet_id in tweet_ids:
        cleaned = tweet_id.strip()
        if not cleaned.isdigit() or cleaned in seen:
            continue
        seen.add(cleaned)
        cleaned_ids.append(cleaned)

    if not cleaned_ids:
        return []

    payload = _request_json(
        "GET",
        f"{X_API_BASE}/tweets",
        access_token=access_token,
        allow_app_bearer=True,
        params={
            "ids": ",".join(cleaned_ids),
            "expansions": X_TWEET_EXPANSIONS,
            "tweet.fields": X_TWEET_FIELDS,
            "user.fields": X_USER_FIELDS,
        },
    )

    data = payload.get("data")
    includes = payload.get("includes") if isinstance(payload.get("includes"), dict) else {}
    users = includes.get("users") if isinstance(includes, dict) else []
    lookup = _user_lookup(users)

    mapped_by_id: dict[str, XTweet] = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            mapped = _map_tweet(item, lookup)
            if mapped:
                mapped_by_id[mapped.id] = mapped

    return [mapped_by_id[tweet_id] for tweet_id in cleaned_ids if tweet_id in mapped_by_id]


def build_tweet_processing_text(tweet: XTweet) -> str:
    """Return the richest available text payload for tweet processing."""
    article_title = _optional_string(tweet.article_title)
    article_text = _optional_string(tweet.article_text)
    if article_text:
        if (
            article_title
            and article_text != article_title
            and not article_text.startswith(article_title)
        ):
            return f"{article_title}\n\n{article_text}"
        return article_text

    note_tweet_text = _optional_string(tweet.note_tweet_text)
    if note_tweet_text:
        return note_tweet_text

    return tweet.text.strip()


def fetch_bookmarks(
    *,
    access_token: str,
    user_id: str,
    pagination_token: str | None = None,
    max_results: int = 100,
) -> XTweetsPage:
    """Fetch one page of bookmarks for a user."""
    if not user_id.strip():
        raise ValueError("X user id is required for bookmark sync")
    clamped = max(5, min(max_results, 100))
    params: dict[str, str | int] = {
        "max_results": clamped,
        "expansions": "author_id",
        "tweet.fields": X_TWEET_FIELDS,
        "user.fields": X_USER_FIELDS,
    }
    if pagination_token:
        params["pagination_token"] = pagination_token

    return _fetch_tweets_page(
        url=f"{X_API_BASE}/users/{user_id}/bookmarks",
        access_token=access_token,
        params=params,
    )


def fetch_reverse_chronological_timeline(
    *,
    access_token: str,
    user_id: str,
    pagination_token: str | None = None,
    since_id: str | None = None,
    max_results: int = 100,
    exclude: list[str] | None = None,
) -> XTweetsPage:
    """Fetch one page of the authenticated user's home timeline."""
    if not user_id.strip():
        raise ValueError("X user id is required for timeline sync")
    clamped = max(5, min(max_results, 100))
    params: dict[str, Any] = {
        "max_results": clamped,
        "expansions": "author_id",
        "tweet.fields": X_TWEET_FIELDS,
        "user.fields": X_USER_FIELDS,
    }
    if pagination_token:
        params["pagination_token"] = pagination_token
    if since_id:
        params["since_id"] = since_id
    if exclude:
        params["exclude"] = ",".join(value for value in exclude if value)

    return _fetch_tweets_page(
        url=f"{X_API_BASE}/users/{user_id}/timelines/reverse_chronological",
        access_token=access_token,
        params=params,
    )


def fetch_list_tweets(
    *,
    list_id: str,
    access_token: str | None = None,
    pagination_token: str | None = None,
    max_results: int = 100,
) -> XTweetsPage:
    """Fetch one page of tweets for a list."""
    cleaned = list_id.strip()
    if not cleaned:
        raise ValueError("List id is required")
    clamped = max(5, min(max_results, 100))
    params: dict[str, Any] = {
        "max_results": clamped,
        "expansions": "author_id",
        "tweet.fields": X_TWEET_FIELDS,
        "user.fields": X_USER_FIELDS,
    }
    if pagination_token:
        params["pagination_token"] = pagination_token

    return _fetch_tweets_page(
        url=f"{X_API_BASE}/lists/{cleaned}/tweets",
        access_token=access_token,
        allow_app_bearer=True,
        params=params,
    )


def fetch_user_tweets(
    *,
    user_id: str,
    access_token: str | None = None,
    pagination_token: str | None = None,
    max_results: int = 100,
    exclude: list[str] | None = None,
) -> XTweetsPage:
    """Fetch one page of tweets for a user."""
    cleaned = user_id.strip()
    if not cleaned:
        raise ValueError("User id is required")
    clamped = max(5, min(max_results, 100))
    params: dict[str, Any] = {
        "max_results": clamped,
        "expansions": X_TWEET_EXPANSIONS,
        "tweet.fields": X_TWEET_FIELDS,
        "user.fields": X_USER_FIELDS,
    }
    if pagination_token:
        params["pagination_token"] = pagination_token
    if exclude:
        params["exclude"] = ",".join(value for value in exclude if value)

    return _fetch_tweets_page(
        url=f"{X_API_BASE}/users/{cleaned}/tweets",
        access_token=access_token,
        allow_app_bearer=True,
        params=params,
    )


def search_recent_tweets(
    *,
    query: str,
    access_token: str | None = None,
    next_token: str | None = None,
    max_results: int = 100,
) -> XTweetsPage:
    """Search recent tweets."""
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("Search query is required")
    clamped = max(10, min(max_results, 100))
    params: dict[str, Any] = {
        "query": cleaned_query,
        "max_results": clamped,
        "expansions": X_TWEET_EXPANSIONS,
        "tweet.fields": X_TWEET_FIELDS,
        "user.fields": X_USER_FIELDS,
    }
    if next_token:
        params["next_token"] = next_token

    return _fetch_tweets_page(
        url=f"{X_API_BASE}/tweets/search/recent",
        access_token=access_token,
        allow_app_bearer=True,
        params=params,
    )


def fetch_owned_lists(
    *,
    access_token: str,
    user_id: str,
    pagination_token: str | None = None,
    max_results: int = 100,
) -> XListsPage:
    """Fetch one page of lists owned by a user."""
    return _fetch_lists_page(
        url=f"{X_API_BASE}/users/{user_id}/owned_lists",
        access_token=access_token,
        pagination_token=pagination_token,
        max_results=max_results,
    )


def fetch_followed_lists(
    *,
    access_token: str,
    user_id: str,
    pagination_token: str | None = None,
    max_results: int = 100,
) -> XListsPage:
    """Fetch one page of lists followed by a user."""
    return _fetch_lists_page(
        url=f"{X_API_BASE}/users/{user_id}/followed_lists",
        access_token=access_token,
        pagination_token=pagination_token,
        max_results=max_results,
    )


def _fetch_tweets_page(
    *,
    url: str,
    access_token: str | None,
    params: dict[str, Any],
    allow_app_bearer: bool = False,
) -> XTweetsPage:
    payload = _request_json(
        "GET",
        url,
        access_token=access_token,
        allow_app_bearer=allow_app_bearer,
        params=params,
    )

    data = payload.get("data")
    includes = payload.get("includes") if isinstance(payload.get("includes"), dict) else {}
    users = includes.get("users") if isinstance(includes, dict) else []
    lookup = _user_lookup(users)

    tweets: list[XTweet] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            mapped = _map_tweet(item, lookup)
            if mapped:
                tweets.append(mapped)

    return XTweetsPage(
        tweets=tweets,
        next_token=_extract_next_token(payload.get("meta")),
    )


def _fetch_lists_page(
    *,
    url: str,
    access_token: str,
    pagination_token: str | None,
    max_results: int,
) -> XListsPage:
    clamped = max(5, min(max_results, 100))
    params: dict[str, Any] = {"max_results": clamped}
    if pagination_token:
        params["pagination_token"] = pagination_token

    payload = _request_json(
        "GET",
        url,
        access_token=access_token,
        params=params,
    )
    data = payload.get("data")
    lists: list[XList] = []
    if isinstance(data, list):
        for item in data:
            mapped = _map_list(item)
            if mapped:
                lists.append(mapped)

    return XListsPage(
        lists=lists,
        next_token=_extract_next_token(payload.get("meta")),
    )


def _oauth_token_request(*, grant_type: str, extra: dict[str, str]) -> dict[str, Any]:
    settings = get_settings()
    client_id = (settings.x_client_id or "").strip()
    if not client_id:
        raise ValueError("X OAuth is not configured (X_CLIENT_ID is required)")

    payload = {
        "grant_type": grant_type,
        "client_id": client_id,
        **extra,
    }
    if grant_type == "authorization_code":
        redirect_uri = (settings.x_oauth_redirect_uri or "").strip()
        if not redirect_uri:
            raise ValueError(
                "X OAuth is not configured (X_OAUTH_REDIRECT_URI is required)"
            )
        payload["redirect_uri"] = redirect_uri
    auth: tuple[str, str] | None = None
    client_secret = (settings.x_client_secret or "").strip()
    if client_secret:
        auth = (client_id, client_secret)

    return _request_json(
        "POST",
        settings.x_oauth_token_url,
        access_token=None,
        data=payload,
        auth=auth,
    )


def _request_json(
    method: str,
    url: str,
    *,
    access_token: str | None,
    allow_app_bearer: bool = False,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    auth: tuple[str, str] | None = None,
) -> dict[str, Any]:
    token = _resolve_access_token(access_token, allow_app_bearer=allow_app_bearer)
    headers: dict[str, str] = {"accept": "application/json"}
    if token:
        headers["authorization"] = _as_bearer(token)
    if data is not None:
        headers["content-type"] = "application/x-www-form-urlencoded"

    with httpx.Client(timeout=20.0) as client:
        response = client.request(
            method,
            url,
            headers=headers,
            params=params,
            data=data,
            auth=auth,
        )

    if response.status_code >= 400:
        detail = _extract_error_text(response)
        logger.error(
            "X API request failed",
            extra={
                "component": "x_api",
                "operation": "request",
                "context_data": {
                    "method": method,
                    "url": url,
                    "status_code": response.status_code,
                    "detail": detail,
                },
            },
        )
        raise RuntimeError(f"X API {response.status_code}: {detail}")

    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to parse X API response JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected non-object JSON payload from X API")
    return payload


def _resolve_access_token(access_token: str | None, *, allow_app_bearer: bool) -> str | None:
    if access_token and access_token.strip():
        return access_token.strip()
    if not allow_app_bearer:
        return None
    settings = get_settings()
    app_token = (settings.x_app_bearer_token or "").strip()
    if not app_token:
        raise ValueError("X_APP_BEARER_TOKEN is required for app-authenticated X requests")
    return app_token


def _as_bearer(token: str) -> str:
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


def _parse_token_payload(payload: dict[str, Any]) -> XTokenResponse:
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("X token response missing access_token")
    refresh_token = _optional_string(payload.get("refresh_token"))
    expires_in = payload.get("expires_in")
    expires_in_int: int | None = None
    if isinstance(expires_in, int):
        expires_in_int = expires_in
    elif isinstance(expires_in, str) and expires_in.isdigit():
        expires_in_int = int(expires_in)

    scope_value = payload.get("scope")
    scopes: list[str] = []
    if isinstance(scope_value, str):
        scopes = [item for item in scope_value.split(" ") if item]
    elif isinstance(scope_value, list):
        scopes = [item.strip() for item in scope_value if isinstance(item, str) and item.strip()]

    return XTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in_int,
        scopes=scopes,
    )


def _extract_error_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        text = response.text.strip()
        return text[:300] if text else "Unknown error"

    if isinstance(payload, dict):
        if isinstance(payload.get("title"), str) and isinstance(payload.get("detail"), str):
            return f"{payload['title']}: {payload['detail']}"
        if isinstance(payload.get("detail"), str):
            return payload["detail"]
        if isinstance(payload.get("error"), str):
            return payload["error"]
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                msg = first.get("message") or first.get("detail") or first.get("title")
                if isinstance(msg, str):
                    return msg
    return "Unknown error"


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _user_lookup(raw_users: Any) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_users, list):
        return lookup
    for user in raw_users:
        if not isinstance(user, dict):
            continue
        user_id = _optional_string(user.get("id"))
        if not user_id:
            continue
        lookup[user_id] = user
    return lookup


def _map_tweet(tweet_data: dict[str, Any], users_by_id: dict[str, dict[str, Any]]) -> XTweet | None:
    tweet_id = _optional_string(tweet_data.get("id"))
    article_title, article_text = _extract_article_parts(tweet_data.get("article"))
    note_tweet_text = _extract_note_tweet_text(tweet_data.get("note_tweet"))
    text = (
        _optional_string(tweet_data.get("text"))
        or note_tweet_text
        or article_title
        or article_text
    )
    if not tweet_id or not text:
        return None

    author_id = _optional_string(tweet_data.get("author_id"))
    author_data = users_by_id.get(author_id or "", {})
    username = _optional_string(author_data.get("username"))
    name = _optional_string(author_data.get("name")) or username

    metrics = (
        tweet_data.get("public_metrics")
        if isinstance(tweet_data.get("public_metrics"), dict)
        else {}
    )
    entities = tweet_data.get("entities") if isinstance(tweet_data.get("entities"), dict) else {}

    return XTweet(
        id=tweet_id,
        text=text,
        author_id=author_id,
        author_username=username,
        author_name=name,
        created_at=_optional_string(tweet_data.get("created_at")),
        like_count=_metric_int(metrics, "like_count"),
        retweet_count=_metric_int(metrics, "retweet_count"),
        reply_count=_metric_int(metrics, "reply_count"),
        conversation_id=_optional_string(tweet_data.get("conversation_id")),
        in_reply_to_user_id=_optional_string(tweet_data.get("in_reply_to_user_id")),
        referenced_tweet_types=_referenced_tweet_types(tweet_data.get("referenced_tweets")),
        article_title=article_title,
        article_text=article_text,
        note_tweet_text=note_tweet_text,
        external_urls=_extract_external_urls(entities),
        linked_tweet_ids=_extract_linked_tweet_ids(tweet_data, entities),
    )


def _map_list(list_data: Any) -> XList | None:
    if not isinstance(list_data, dict):
        return None
    list_id = _optional_string(list_data.get("id"))
    name = _optional_string(list_data.get("name"))
    if not list_id or not name:
        return None
    return XList(id=list_id, name=name)


def _metric_int(metrics: dict[str, Any], key: str) -> int | None:
    value = metrics.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _referenced_tweet_types(raw_references: Any) -> list[str]:
    if not isinstance(raw_references, list):
        return []
    values: list[str] = []
    for reference in raw_references:
        if not isinstance(reference, dict):
            continue
        reference_type = _optional_string(reference.get("type"))
        if reference_type:
            values.append(reference_type)
    return values


def _referenced_tweet_ids(raw_references: Any) -> list[str]:
    if not isinstance(raw_references, list):
        return []
    seen: set[str] = set()
    values: list[str] = []
    for reference in raw_references:
        if not isinstance(reference, dict):
            continue
        tweet_id = _optional_string(reference.get("id"))
        if not tweet_id or not tweet_id.isdigit() or tweet_id in seen:
            continue
        seen.add(tweet_id)
        values.append(tweet_id)
    return values


def _extract_next_token(meta: Any) -> str | None:
    if not isinstance(meta, dict):
        return None
    token = meta.get("next_token")
    if isinstance(token, str) and token.strip():
        return token.strip()
    return None


def _extract_external_urls(entities: dict[str, Any]) -> list[str]:
    raw_urls = entities.get("urls")
    if not isinstance(raw_urls, list):
        return []
    seen: set[str] = set()
    urls: list[str] = []
    for item in raw_urls:
        if not isinstance(item, dict):
            continue
        raw_url = item.get("expanded_url") or item.get("unwound_url") or item.get("url")
        normalized = _normalize_external_url(raw_url if isinstance(raw_url, str) else "")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
    return urls


def _extract_linked_tweet_ids(tweet_data: dict[str, Any], entities: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    linked_ids: list[str] = []

    for tweet_id in _referenced_tweet_ids(tweet_data.get("referenced_tweets")):
        if tweet_id in seen:
            continue
        seen.add(tweet_id)
        linked_ids.append(tweet_id)

    raw_urls = entities.get("urls")
    if not isinstance(raw_urls, list):
        return linked_ids

    for item in raw_urls:
        if not isinstance(item, dict):
            continue
        raw_url = item.get("expanded_url") or item.get("unwound_url") or item.get("url")
        if not isinstance(raw_url, str):
            continue
        tweet_id = extract_tweet_id(raw_url)
        if not tweet_id or tweet_id in seen:
            continue
        seen.add(tweet_id)
        linked_ids.append(tweet_id)

    return linked_ids


def _normalize_external_url(raw_url: str) -> str | None:
    cleaned = raw_url.strip()
    if not cleaned:
        return None
    try:
        parsed = urlparse(cleaned)
    except Exception:
        return None

    if not parsed.netloc:
        return None
    host = (parsed.hostname or "").lower().strip(".")
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    if (
        _is_domain_or_subdomain(host, "x.com")
        or _is_domain_or_subdomain(host, "twitter.com")
        or _is_domain_or_subdomain(host, "t.co")
    ):
        return None

    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "https"
    normalized = parsed._replace(scheme=scheme).geturl()
    if normalized.startswith("http://"):
        normalized = "https://" + normalized[len("http://") :]
    return normalized


def _first_text(*candidates: Any) -> str | None:
    for candidate in candidates:
        if isinstance(candidate, str):
            cleaned = candidate.strip()
            if cleaned:
                return cleaned
    return None


def _nested_text(node: Any, *path: str) -> str | None:
    current = node
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _first_text(current)


def _collect_text_fields(node: Any, keys: set[str], output: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys and isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    output.append(cleaned)
                continue
            _collect_text_fields(value, keys, output)
        return

    if isinstance(node, list):
        for item in node:
            _collect_text_fields(item, keys, output)


def _extract_article_parts(article_data: Any) -> tuple[str | None, str | None]:
    if not isinstance(article_data, dict):
        return None, None

    article_result = article_data.get("article_results", {}).get("result")
    if not isinstance(article_result, dict):
        result = article_data.get("result")
        article_result = result if isinstance(result, dict) else article_data

    title = _first_text(
        article_result.get("title"),
        article_result.get("headline"),
        article_data.get("title"),
        article_data.get("headline"),
    )
    body = _first_text(
        article_result.get("plain_text"),
        article_data.get("plain_text"),
        _nested_text(article_result, "body", "text"),
        _nested_text(article_result, "body", "richtext", "text"),
        _nested_text(article_result, "body", "rich_text", "text"),
        _nested_text(article_result, "content", "text"),
        _nested_text(article_result, "content", "richtext", "text"),
        _nested_text(article_result, "content", "rich_text", "text"),
        article_result.get("text"),
        _nested_text(article_result, "richtext", "text"),
        _nested_text(article_result, "rich_text", "text"),
        _nested_text(article_data, "body", "text"),
        _nested_text(article_data, "body", "richtext", "text"),
        _nested_text(article_data, "body", "rich_text", "text"),
        _nested_text(article_data, "content", "text"),
        _nested_text(article_data, "content", "richtext", "text"),
        _nested_text(article_data, "content", "rich_text", "text"),
        article_data.get("text"),
        _nested_text(article_data, "richtext", "text"),
        _nested_text(article_data, "rich_text", "text"),
    )

    if body and title and body == title:
        body = None

    if not body:
        collected: list[str] = []
        _collect_text_fields(article_result, {"plain_text", "text"}, collected)
        _collect_text_fields(article_data, {"plain_text", "text"}, collected)
        unique: list[str] = []
        seen: set[str] = set()
        for item in collected:
            if title and item == title:
                continue
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        if unique:
            body = "\n\n".join(unique)

    return title, body


def _extract_note_tweet_text(note_data: Any) -> str | None:
    if not isinstance(note_data, dict):
        return None

    note_result = note_data.get("note_tweet_results", {}).get("result")
    if not isinstance(note_result, dict):
        result = note_data.get("result")
        note_result = result if isinstance(result, dict) else note_data

    return _first_text(
        note_result.get("text"),
        _nested_text(note_result, "richtext", "text"),
        _nested_text(note_result, "rich_text", "text"),
        _nested_text(note_result, "content", "text"),
        _nested_text(note_result, "content", "richtext", "text"),
        _nested_text(note_result, "content", "rich_text", "text"),
        note_data.get("text"),
        _nested_text(note_data, "richtext", "text"),
        _nested_text(note_data, "rich_text", "text"),
        _nested_text(note_data, "content", "text"),
        _nested_text(note_data, "content", "richtext", "text"),
        _nested_text(note_data, "content", "rich_text", "text"),
    )


def _is_domain_or_subdomain(host: str, domain: str) -> bool:
    if host == domain:
        return True
    return host.endswith(f".{domain}")
