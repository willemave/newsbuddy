"""Service layer for user-specific X integration state and sync."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import (
    CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY,
)
from app.core.db import run_with_sqlite_lock_retry
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.content_submission import SubmitContentRequest
from app.models.contracts import TaskType
from app.models.schema import UserIntegrationConnection, UserIntegrationSyncState
from app.models.user import User
from app.services.content_submission import submit_user_content
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.news_digest_preferences import resolve_user_news_digest_preference_prompt
from app.services.news_ingestion import (
    build_news_item_upsert_input_from_scraped_item,
    upsert_news_item,
)
from app.services.token_crypto import decrypt_token, encrypt_token
from app.services.twitter_share import canonical_tweet_url
from app.services.x_api import (
    X_DEFAULT_SCOPES,
    XList,
    XTweet,
    build_oauth_authorize_url,
    exchange_oauth_code,
    fetch_bookmarks,
    fetch_followed_lists,
    fetch_list_tweets,
    fetch_owned_lists,
    fetch_reverse_chronological_timeline,
    get_authenticated_user,
    refresh_oauth_token,
)
from app.services.x_digest_filter import (
    X_DIGEST_FILTER_THRESHOLD,
    XDigestFilterDecision,
    score_x_digest_candidate,
)

logger = get_logger(__name__)

X_PROVIDER = "x"
OAUTH_PENDING_KEY = "oauth_pending"
OAUTH_PENDING_TTL_MINUTES = 20
TOKEN_EXPIRY_SKEW_SECONDS = 60
BOOKMARK_SYNC_MAX_PAGES = 5
BOOKMARK_SYNC_PAGE_SIZE = 100
TIMELINE_SYNC_MAX_PAGES = 5
TIMELINE_SYNC_PAGE_SIZE = 100
LIST_DISCOVERY_MAX_PAGES = 5
LIST_DISCOVERY_PAGE_SIZE = 100
LIST_SYNC_MAX_PAGES = 5
LIST_SYNC_PAGE_SIZE = 100
TIMELINE_EXCLUDE_TYPES = ("replies", "retweets")
TIMELINE_CHANNEL = "timeline"
LISTS_CHANNEL = "lists"
BOOKMARKS_CHANNEL = "bookmarks"
REQUIRED_TIMELINE_SCOPES = frozenset({"tweet.read", "users.read"})
REQUIRED_LIST_SCOPES = frozenset({"tweet.read", "users.read", "list.read"})
USERNAME_REGEX = re.compile(r"^[A-Za-z0-9_]{1,15}$")


@dataclass(frozen=True)
class XConnectionView:
    """Normalized connection payload for API responses."""

    provider: str
    connected: bool
    is_active: bool
    provider_user_id: str | None
    provider_username: str | None
    scopes: list[str]
    last_synced_at: datetime | None
    last_status: str | None
    last_error: str | None
    twitter_username: str | None


@dataclass(frozen=True)
class XSyncChannelSummary:
    """Summary for one X sync channel."""

    status: str
    fetched: int
    accepted: int
    filtered_out: int
    errored: int
    created: int
    reused: int
    newest_item_id: str | None = None


@dataclass(frozen=True)
class XSyncSummary:
    """Summary for one combined X sync run."""

    status: str
    fetched: int
    accepted: int
    filtered_out: int
    errored: int
    created: int
    reused: int
    channels: dict[str, XSyncChannelSummary]


def normalize_twitter_username(username: str | None) -> str | None:
    """Normalize user-provided X username to canonical form.

    Args:
        username: Raw username value from request/UI.

    Returns:
        Lowercased username without leading @, or None when empty.

    Raises:
        ValueError: If the non-empty input is not a valid username.
    """
    if username is None:
        return None
    cleaned = username.strip()
    if not cleaned:
        return None
    if cleaned.startswith("@"):
        cleaned = cleaned[1:]
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    if not USERNAME_REGEX.fullmatch(cleaned):
        raise ValueError("Twitter username must be 1-15 chars (letters, numbers, underscore)")
    return cleaned.lower()


def is_x_oauth_configured() -> bool:
    """Return whether required X OAuth configuration is available."""
    settings = get_settings()
    return bool(
        (settings.x_client_id or "").strip()
        and (settings.x_oauth_redirect_uri or "").strip()
        and (settings.x_token_encryption_key or "").strip()
    )


def has_active_x_connection(db: Session, user_id: int) -> bool:
    """Return True when a user has an active X bookmark connection."""
    connection = _get_connection(db, user_id=user_id)
    return bool(connection and connection.is_active and connection.access_token_encrypted)


def get_x_user_access_token(db: Session, *, user_id: int) -> str | None:
    """Return a valid decrypted user access token when connection is active."""
    connection = _get_connection(db, user_id=user_id)
    if not connection or not connection.is_active:
        return None
    try:
        return _ensure_valid_access_token(db, connection)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Unable to get user access token for X connection",
            extra={
                "component": "x_integration",
                "operation": "get_access_token",
                "item_id": str(user_id),
                "context_data": {"error": str(exc)},
            },
        )
        return None


def get_x_connection_view(db: Session, user: User) -> XConnectionView:
    """Build a normalized X connection view for API responses."""
    connection = _get_connection(db, user_id=user.id)
    sync_state = _get_sync_state(db, connection_id=connection.id) if connection else None
    connected = bool(connection and connection.is_active and connection.access_token_encrypted)

    return XConnectionView(
        provider=X_PROVIDER,
        connected=connected,
        is_active=bool(connection and connection.is_active),
        provider_user_id=connection.provider_user_id if connection else None,
        provider_username=connection.provider_username if connection else None,
        scopes=_normalize_scopes(connection.scopes if connection else None),
        last_synced_at=sync_state.last_synced_at if sync_state else None,
        last_status=sync_state.last_status if sync_state else None,
        last_error=sync_state.last_error if sync_state else None,
        twitter_username=user.twitter_username,
    )


def start_x_oauth(
    db: Session,
    *,
    user: User,
    twitter_username: str | None = None,
) -> tuple[str, str, list[str]]:
    """Start X OAuth flow and persist PKCE/state metadata."""
    if not is_x_oauth_configured():
        raise ValueError(
            "X OAuth is not configured. Set X_CLIENT_ID, X_OAUTH_REDIRECT_URI, and "
            "X_TOKEN_ENCRYPTION_KEY."
        )

    normalized_username = normalize_twitter_username(twitter_username)
    if normalized_username is not None:
        user.twitter_username = normalized_username

    connection = _get_or_create_connection(db, user_id=user.id)
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _build_pkce_code_challenge(code_verifier)
    scopes = list(X_DEFAULT_SCOPES)

    metadata = dict(connection.connection_metadata or {})
    metadata[OAUTH_PENDING_KEY] = {
        "state": state,
        "code_verifier": code_verifier,
        "created_at": _now_utc_iso(),
    }

    connection.scopes = scopes
    connection.connection_metadata = metadata
    db.commit()

    authorize_url = build_oauth_authorize_url(
        state=state,
        code_challenge=code_challenge,
        scopes=scopes,
    )
    return authorize_url, state, scopes


def exchange_x_oauth(
    db: Session,
    *,
    user: User,
    code: str,
    state: str,
) -> XConnectionView:
    """Finalize X OAuth code exchange and persist encrypted tokens."""
    connection = _get_connection(db, user_id=user.id)
    if not connection:
        raise ValueError("OAuth flow not initialized. Start OAuth first.")

    metadata = dict(connection.connection_metadata or {})
    pending = metadata.get(OAUTH_PENDING_KEY)
    if not isinstance(pending, dict):
        raise ValueError("OAuth flow expired or missing. Start OAuth again.")

    expected_state = pending.get("state")
    code_verifier = pending.get("code_verifier")
    created_at = pending.get("created_at")
    if not isinstance(expected_state, str) or not isinstance(code_verifier, str):
        raise ValueError("Invalid OAuth pending state. Start OAuth again.")
    if expected_state != state:
        raise ValueError("Invalid OAuth state")
    if _pending_state_expired(created_at):
        raise ValueError("OAuth flow expired. Start OAuth again.")

    token = exchange_oauth_code(code=code, code_verifier=code_verifier)
    me = get_authenticated_user(access_token=token.access_token)

    metadata.pop(OAUTH_PENDING_KEY, None)
    metadata["connected_at"] = _now_utc_iso()

    connection.provider_user_id = me.id
    connection.provider_username = me.username
    connection.access_token_encrypted = encrypt_token(token.access_token)
    connection.refresh_token_encrypted = (
        encrypt_token(token.refresh_token) if token.refresh_token else None
    )
    connection.token_expires_at = _expires_at_from_seconds(token.expires_in)
    connection.scopes = token.scopes or list(X_DEFAULT_SCOPES)
    connection.is_active = True
    connection.connection_metadata = metadata

    if me.username:
        user.twitter_username = normalize_twitter_username(me.username)

    sync_state = _get_or_create_sync_state(db, connection_id=connection.id)
    if not sync_state.last_status:
        sync_state.last_status = "connected"
    sync_state.last_error = None

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.exception(
            "Failed to save X OAuth exchange due to integrity error",
            extra={
                "component": "x_integration",
                "operation": "oauth_exchange",
                "item_id": str(user.id),
                "context_data": {"error": str(exc)},
            },
        )
        raise ValueError("This X account is already linked to another user.") from exc

    db.refresh(user)
    return get_x_connection_view(db, user)


def disconnect_x_connection(db: Session, *, user: User) -> XConnectionView:
    """Disable an X connection and clear stored tokens."""
    connection = _get_connection(db, user_id=user.id)
    if connection:
        metadata = dict(connection.connection_metadata or {})
        metadata.pop(OAUTH_PENDING_KEY, None)
        metadata["disconnected_at"] = _now_utc_iso()

        connection.is_active = False
        connection.access_token_encrypted = None
        connection.refresh_token_encrypted = None
        connection.token_expires_at = None
        connection.connection_metadata = metadata

        sync_state = _get_or_create_sync_state(db, connection_id=connection.id)
        sync_state.last_status = "disconnected"
        sync_state.last_error = None
        sync_state.last_synced_at = _now_naive_utc()
        db.commit()

    return get_x_connection_view(db, user)


def sync_x_sources_for_user(db: Session, *, user_id: int, force: bool = False) -> XSyncSummary:
    """Sync bookmarks plus digest-source X content for a connected user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")

    connection = _get_connection(db, user_id=user.id)
    if not connection or not connection.is_active:
        return XSyncSummary(
            status="not_connected",
            fetched=0,
            accepted=0,
            filtered_out=0,
            errored=0,
            created=0,
            reused=0,
            channels={},
        )

    sync_state = _get_or_create_sync_state(db, connection_id=connection.id)
    if not force and _should_skip_scheduled_sync(sync_state):
        return XSyncSummary(
            status="skipped_recently",
            fetched=0,
            accepted=0,
            filtered_out=0,
            errored=0,
            created=0,
            reused=0,
            channels={},
        )
    existing_sync_metadata = (
        dict(sync_state.sync_metadata) if isinstance(sync_state.sync_metadata, dict) else {}
    )
    filter_prompt = resolve_user_news_digest_preference_prompt(user)

    try:
        access_token = _ensure_valid_access_token(db, connection)
        provider_user_id = _ensure_provider_user_id(
            db,
            user=user,
            connection=connection,
            access_token=access_token,
        )
        bookmark_summary = _sync_bookmark_channel(
            db,
            user=user,
            access_token=access_token,
            provider_user_id=provider_user_id,
            existing_sync_metadata=existing_sync_metadata,
        )
        channel_errors: list[str] = []
        try:
            timeline_summary = _sync_timeline_channel(
                db,
                user=user,
                access_token=access_token,
                provider_user_id=provider_user_id,
                connection=connection,
                existing_sync_metadata=existing_sync_metadata,
                filter_prompt=filter_prompt,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "X timeline sync failed",
                extra={
                    "component": "x_integration",
                    "operation": "sync_timeline",
                    "item_id": user.id,
                    "context_data": {"error": str(exc)},
                },
            )
            channel_errors.append(f"{TIMELINE_CHANNEL}: {exc}")
            timeline_summary = _failed_channel_summary()

        try:
            lists_summary, list_state_updates = _sync_lists_channel(
                db,
                user=user,
                access_token=access_token,
                provider_user_id=provider_user_id,
                connection=connection,
                existing_sync_metadata=existing_sync_metadata,
                filter_prompt=filter_prompt,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "X list sync failed",
                extra={
                    "component": "x_integration",
                    "operation": "sync_lists",
                    "item_id": user.id,
                    "context_data": {"error": str(exc)},
                },
            )
            channel_errors.append(f"{LISTS_CHANNEL}: {exc}")
            lists_summary = _failed_channel_summary()
            list_state_updates = {}

        channel_summaries = {
            BOOKMARKS_CHANNEL: bookmark_summary,
            TIMELINE_CHANNEL: timeline_summary,
            LISTS_CHANNEL: lists_summary,
        }
        total_fetched = sum(channel.fetched for channel in channel_summaries.values())
        total_accepted = sum(channel.accepted for channel in channel_summaries.values())
        total_filtered_out = sum(channel.filtered_out for channel in channel_summaries.values())
        total_errored = sum(channel.errored for channel in channel_summaries.values())
        total_created = sum(channel.created for channel in channel_summaries.values())
        total_reused = sum(channel.reused for channel in channel_summaries.values())
        overall_status = _resolve_combined_sync_status(channel_summaries)

        sync_state.last_synced_at = _now_naive_utc()
        sync_state.last_status = overall_status
        sync_state.last_error = "; ".join(channel_errors)[:2000] if channel_errors else None
        bookmark_newest = (
            bookmark_summary.newest_item_id
            or timeline_summary.newest_item_id
            or lists_summary.newest_item_id
        )
        if bookmark_newest:
            sync_state.last_synced_item_id = bookmark_newest
        sync_state.cursor = None
        sync_state.sync_metadata = _build_sync_metadata_payload(
            existing_sync_metadata=existing_sync_metadata,
            bookmark_summary=bookmark_summary,
            timeline_summary=timeline_summary,
            lists_summary=lists_summary,
            list_state_updates=list_state_updates,
        )
        db.commit()

        return XSyncSummary(
            status=overall_status,
            fetched=total_fetched,
            accepted=total_accepted,
            filtered_out=total_filtered_out,
            errored=total_errored,
            created=total_created,
            reused=total_reused,
            channels=channel_summaries,
        )

    except Exception as exc:  # noqa: BLE001
        sync_state.last_synced_at = _now_naive_utc()
        sync_state.last_status = "failed"
        sync_state.last_error = str(exc)[:2000]
        db.commit()
        raise


def sync_x_bookmarks_for_user(db: Session, *, user_id: int) -> XSyncSummary:
    """Backward-compatible wrapper for the newer combined X sync."""
    return sync_x_sources_for_user(db, user_id=user_id)


def _sync_bookmark_channel(
    db: Session,
    *,
    user: User,
    access_token: str,
    provider_user_id: str,
    existing_sync_metadata: dict[str, Any],
) -> XSyncChannelSummary:
    bookmark_state = _get_channel_state(existing_sync_metadata, BOOKMARKS_CHANNEL)
    last_synced_id = _clean_optional_string(bookmark_state.get("last_synced_item_id"))
    newest_seen_id: str | None = None
    fetched = 0
    collected_new: list[str] = []
    next_token: str | None = None
    reached_previous_sync = False

    for _ in range(BOOKMARK_SYNC_MAX_PAGES):
        page = fetch_bookmarks(
            access_token=access_token,
            user_id=provider_user_id,
            pagination_token=next_token,
            max_results=BOOKMARK_SYNC_PAGE_SIZE,
        )
        if page.tweets and newest_seen_id is None:
            newest_seen_id = page.tweets[0].id

        fetched += len(page.tweets)
        if not page.tweets:
            break

        for tweet in page.tweets:
            if last_synced_id and tweet.id == last_synced_id:
                reached_previous_sync = True
                break
            collected_new.append(tweet.id)

        if reached_previous_sync or not page.next_token:
            break
        next_token = page.next_token

    created = 0
    reused = 0
    for tweet_id in reversed(collected_new):
        result = submit_user_content(
            db,
            SubmitContentRequest(
                url=canonical_tweet_url(tweet_id),
                platform="twitter",
            ),
            user,
            submitted_via="x_bookmarks",
        )
        if result.already_exists:
            reused += 1
        else:
            created += 1

    return XSyncChannelSummary(
        status="success",
        fetched=fetched,
        accepted=created + reused,
        filtered_out=0,
        errored=0,
        created=created,
        reused=reused,
        newest_item_id=newest_seen_id,
    )


def _sync_timeline_channel(
    db: Session,
    *,
    user: User,
    access_token: str,
    provider_user_id: str,
    connection: UserIntegrationConnection,
    existing_sync_metadata: dict[str, Any],
    filter_prompt: str,
) -> XSyncChannelSummary:
    if _missing_required_scopes(connection, REQUIRED_TIMELINE_SCOPES):
        return XSyncChannelSummary(
            status="missing_scopes",
            fetched=0,
            accepted=0,
            filtered_out=0,
            errored=0,
            created=0,
            reused=0,
            newest_item_id=None,
        )

    timeline_state = _get_channel_state(existing_sync_metadata, TIMELINE_CHANNEL)
    since_id = _clean_optional_string(timeline_state.get("last_synced_item_id"))
    newest_seen_id: str | None = None
    fetched = 0
    accepted = 0
    filtered_out = 0
    errored = 0
    created = 0
    reused = 0
    next_token: str | None = None
    seen_ids: set[str] = set()

    for _ in range(TIMELINE_SYNC_MAX_PAGES):
        page = fetch_reverse_chronological_timeline(
            access_token=access_token,
            user_id=provider_user_id,
            pagination_token=next_token,
            since_id=since_id,
            max_results=TIMELINE_SYNC_PAGE_SIZE,
            exclude=list(TIMELINE_EXCLUDE_TYPES),
        )
        if page.tweets and newest_seen_id is None:
            newest_seen_id = page.tweets[0].id

        fetched += len(page.tweets)
        if not page.tweets:
            break

        fresh_tweets = [
            tweet
            for tweet in page.tweets
            if tweet.id not in seen_ids and _should_ingest_digest_tweet(tweet)
        ]
        filtered_out += sum(1 for tweet in page.tweets if not _should_ingest_digest_tweet(tweet))
        for tweet in fresh_tweets:
            seen_ids.add(tweet.id)
        for tweet in reversed(fresh_tweets):
            decision = score_x_digest_candidate(
                tweet=tweet,
                user_prompt=filter_prompt,
                source_type="x_timeline",
                source_label="X Following",
            )
            if decision.errored:
                errored += 1
            if not decision.accepted:
                filtered_out += 1
                continue
            try:
                was_created = _upsert_x_digest_tweet_content(
                    db,
                    user=user,
                    tweet=tweet,
                    source_type="x_timeline",
                    source_label="X Following",
                    submitted_via="x_timeline",
                    filter_decision=decision,
                    aggregator_metadata={"timeline_type": "reverse_chronological"},
                )
            except Exception as exc:  # noqa: BLE001
                errored += 1
                logger.exception(
                    "Timeline digest tweet upsert failed",
                    extra={
                        "component": "x_integration",
                        "operation": "sync_timeline",
                        "item_id": tweet.id,
                        "context_data": {"error": str(exc)},
                    },
                )
                continue
            accepted += 1
            if was_created:
                created += 1
            else:
                reused += 1

        if not page.next_token:
            break
        next_token = page.next_token

    return XSyncChannelSummary(
        status=_resolve_channel_status(errored),
        fetched=fetched,
        accepted=accepted,
        filtered_out=filtered_out,
        errored=errored,
        created=created,
        reused=reused,
        newest_item_id=newest_seen_id,
    )


def _sync_lists_channel(
    db: Session,
    *,
    user: User,
    access_token: str,
    provider_user_id: str,
    connection: UserIntegrationConnection,
    existing_sync_metadata: dict[str, Any],
    filter_prompt: str,
) -> tuple[XSyncChannelSummary, dict[str, dict[str, Any]]]:
    if _missing_required_scopes(connection, REQUIRED_LIST_SCOPES):
        return XSyncChannelSummary(
            status="missing_scopes",
            fetched=0,
            accepted=0,
            filtered_out=0,
            errored=0,
            created=0,
            reused=0,
            newest_item_id=None,
        ), {}

    discovered_lists = _fetch_all_user_lists(
        access_token=access_token,
        provider_user_id=provider_user_id,
    )
    if not discovered_lists:
        return XSyncChannelSummary(
            status="success",
            fetched=0,
            accepted=0,
            filtered_out=0,
            errored=0,
            created=0,
            reused=0,
            newest_item_id=None,
        ), {}

    lists_state = _get_channel_state(existing_sync_metadata, LISTS_CHANNEL)
    list_states = lists_state.get("list_states")
    list_states = list_states if isinstance(list_states, dict) else {}
    list_state_updates: dict[str, dict[str, Any]] = {}

    total_fetched = 0
    total_accepted = 0
    total_filtered_out = 0
    total_errored = 0
    total_created = 0
    total_reused = 0
    newest_seen_id: str | None = None
    seen_tweet_ids: set[str] = set()

    for x_list in discovered_lists:
        state = list_states.get(x_list.id)
        state = state if isinstance(state, dict) else {}
        last_synced_id = _clean_optional_string(state.get("last_synced_item_id"))
        newest_for_list: str | None = None
        next_token: str | None = None
        reached_previous_sync = False

        for _ in range(LIST_SYNC_MAX_PAGES):
            page = fetch_list_tweets(
                list_id=x_list.id,
                access_token=access_token,
                pagination_token=next_token,
                max_results=LIST_SYNC_PAGE_SIZE,
            )
            if page.tweets and newest_for_list is None:
                newest_for_list = page.tweets[0].id
            if newest_seen_id is None and page.tweets:
                newest_seen_id = page.tweets[0].id

            total_fetched += len(page.tweets)
            if not page.tweets:
                break

            fresh_tweets: list[XTweet] = []
            for tweet in page.tweets:
                if last_synced_id and tweet.id == last_synced_id:
                    reached_previous_sync = True
                    break
                if tweet.id in seen_tweet_ids:
                    continue
                if not _should_ingest_digest_tweet(tweet):
                    total_filtered_out += 1
                    continue
                fresh_tweets.append(tweet)

            for tweet in fresh_tweets:
                seen_tweet_ids.add(tweet.id)
            for tweet in reversed(fresh_tweets):
                decision = score_x_digest_candidate(
                    tweet=tweet,
                    user_prompt=filter_prompt,
                    source_type="x_list",
                    source_label=x_list.name,
                )
                if decision.errored:
                    total_errored += 1
                if not decision.accepted:
                    total_filtered_out += 1
                    continue
                try:
                    was_created = _upsert_x_digest_tweet_content(
                        db,
                        user=user,
                        tweet=tweet,
                        source_type="x_list",
                        source_label=x_list.name,
                        submitted_via="x_list",
                        filter_decision=decision,
                        aggregator_metadata={
                            "list_id": x_list.id,
                            "list_name": x_list.name,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    total_errored += 1
                    logger.exception(
                        "List digest tweet upsert failed",
                        extra={
                            "component": "x_integration",
                            "operation": "sync_lists",
                            "item_id": tweet.id,
                            "context_data": {
                                "list_id": x_list.id,
                                "error": str(exc),
                            },
                        },
                    )
                    continue
                total_accepted += 1
                if was_created:
                    total_created += 1
                else:
                    total_reused += 1

            if reached_previous_sync or not page.next_token:
                break
            next_token = page.next_token

        list_state_updates[x_list.id] = {
            "name": x_list.name,
            "last_synced_item_id": newest_for_list or last_synced_id,
        }

    return XSyncChannelSummary(
        status=_resolve_channel_status(total_errored),
        fetched=total_fetched,
        accepted=total_accepted,
        filtered_out=total_filtered_out,
        errored=total_errored,
        created=total_created,
        reused=total_reused,
        newest_item_id=newest_seen_id,
    ), list_state_updates


def _fetch_all_user_lists(*, access_token: str, provider_user_id: str) -> list[XList]:
    seen_ids: set[str] = set()
    ordered_lists: list[XList] = []

    for fetcher in (fetch_owned_lists, fetch_followed_lists):
        next_token: str | None = None
        for _ in range(LIST_DISCOVERY_MAX_PAGES):
            page = fetcher(
                access_token=access_token,
                user_id=provider_user_id,
                pagination_token=next_token,
                max_results=LIST_DISCOVERY_PAGE_SIZE,
            )
            for x_list in page.lists:
                if x_list.id in seen_ids:
                    continue
                seen_ids.add(x_list.id)
                ordered_lists.append(x_list)
            if not page.next_token:
                break
            next_token = page.next_token

    return ordered_lists


def _upsert_x_digest_tweet_content(
    db: Session,
    *,
    user: User,
    tweet: XTweet,
    source_type: str,
    source_label: str,
    submitted_via: str,
    filter_decision: XDigestFilterDecision,
    aggregator_metadata: dict[str, Any] | None = None,
) -> bool:
    tweet_url = canonical_tweet_url(tweet.id)
    metadata = _build_digest_tweet_metadata(
        tweet=tweet,
        source_type=source_type,
        source_label=source_label,
        submitted_via=submitted_via,
        submitted_by_user_id=user.id,
        filter_decision=filter_decision,
        aggregator_metadata=aggregator_metadata or {},
    )
    payload = build_news_item_upsert_input_from_scraped_item(
        {
            "url": tweet_url,
            "title": _tweet_title(tweet),
            "metadata": metadata,
            "owner_user_id": user.id,
            "visibility_scope": "user",
            "source_type": source_type,
            "source_label": source_label,
            "source_external_id": tweet.id,
        }
    )
    def _persist_news_item() -> tuple[Any, bool]:
        try:
            try:
                news_item, was_created = upsert_news_item(db, payload)
                db.commit()
            except IntegrityError:
                db.rollback()
                news_item, was_created = upsert_news_item(db, payload)
                db.commit()
            db.refresh(news_item)
            return news_item, was_created
        except Exception:
            db.rollback()
            raise

    news_item, was_created = run_with_sqlite_lock_retry(
        db=db,
        component="x_integration",
        operation="upsert_digest_tweet",
        item_id=tweet.id,
        context_data={
            "source_type": source_type,
            "submitted_via": submitted_via,
        },
        work=_persist_news_item,
    )
    queue_gateway = get_task_queue_gateway()
    if was_created or news_item.status != "ready":
        queue_gateway.enqueue(
            TaskType.PROCESS_NEWS_ITEM,
            payload={"news_item_id": news_item.id},
        )
    return was_created


def _build_digest_tweet_metadata(
    *,
    tweet: XTweet,
    source_type: str,
    source_label: str,
    submitted_via: str,
    submitted_by_user_id: int,
    filter_decision: XDigestFilterDecision,
    aggregator_metadata: dict[str, Any],
) -> dict[str, Any]:
    author = tweet.author_name or (f"@{tweet.author_username}" if tweet.author_username else None)
    tweet_url = canonical_tweet_url(tweet.id)
    return {
        "digest_visibility": CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY,
        "platform": "twitter",
        "source_type": source_type,
        "source_label": source_label,
        "source": source_label,
        "discussion_url": tweet_url,
        "submitted_via": submitted_via,
        "submitted_by_user_id": submitted_by_user_id,
        "filter_score": filter_decision.score,
        "filter_reason": filter_decision.reason,
        "filter_threshold": X_DIGEST_FILTER_THRESHOLD,
        "tweet_id": tweet.id,
        "tweet_url": tweet_url,
        "tweet_author": tweet.author_name,
        "tweet_author_username": tweet.author_username,
        "tweet_created_at": tweet.created_at,
        "tweet_like_count": tweet.like_count,
        "tweet_retweet_count": tweet.retweet_count,
        "tweet_reply_count": tweet.reply_count,
        "tweet_text": tweet.text,
        "tweet_external_urls": list(tweet.external_urls),
        "article": {
            "url": tweet_url,
            "title": _tweet_title(tweet),
            "source_domain": "x.com",
        },
        "aggregator": {
            "name": "X",
            "title": tweet.text,
            "url": tweet_url,
            "external_id": tweet.id,
            "author": author,
            "metadata": {
                "likes": tweet.like_count,
                "retweets": tweet.retweet_count,
                "replies": tweet.reply_count,
                **aggregator_metadata,
            },
        },
    }


def _tweet_title(tweet: XTweet) -> str:
    first_line = tweet.text.splitlines()[0].strip() if tweet.text else ""
    if first_line:
        return first_line[:280]
    return f"Post {tweet.id}"


def _should_ingest_digest_tweet(tweet: XTweet) -> bool:
    if not tweet.text.strip():
        return False
    reference_types = set(tweet.referenced_tweet_types)
    if "retweeted" in reference_types:
        return False
    return not tweet.in_reply_to_user_id


def _missing_required_scopes(
    connection: UserIntegrationConnection,
    required_scopes: frozenset[str],
) -> bool:
    connection_scopes = set(_normalize_scopes(connection.scopes))
    return not required_scopes.issubset(connection_scopes)


def _get_channel_state(sync_metadata: dict[str, Any], channel: str) -> dict[str, Any]:
    state = sync_metadata.get(channel)
    return state if isinstance(state, dict) else {}


def _should_skip_scheduled_sync(sync_state: UserIntegrationSyncState) -> bool:
    last_synced_at = sync_state.last_synced_at
    if last_synced_at is None:
        return False
    min_interval_minutes = get_settings().x_sync_min_interval_minutes
    elapsed_seconds = (_now_naive_utc() - last_synced_at).total_seconds()
    return elapsed_seconds < min_interval_minutes * 60


def _resolve_last_synced_item_id(
    previous_state: dict[str, Any],
    newest_item_id: str | None,
) -> str | None:
    if newest_item_id:
        return newest_item_id
    return _clean_optional_string(previous_state.get("last_synced_item_id"))


def _build_sync_metadata_payload(
    *,
    existing_sync_metadata: dict[str, Any],
    bookmark_summary: XSyncChannelSummary,
    timeline_summary: XSyncChannelSummary,
    lists_summary: XSyncChannelSummary,
    list_state_updates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    previous_bookmark_state = _get_channel_state(existing_sync_metadata, BOOKMARKS_CHANNEL)
    previous_timeline_state = _get_channel_state(existing_sync_metadata, TIMELINE_CHANNEL)
    previous_lists_state = _get_channel_state(existing_sync_metadata, LISTS_CHANNEL)
    return {
        BOOKMARKS_CHANNEL: {
            "status": bookmark_summary.status,
            "fetched": bookmark_summary.fetched,
            "accepted": bookmark_summary.accepted,
            "filtered_out": bookmark_summary.filtered_out,
            "errored": bookmark_summary.errored,
            "created": bookmark_summary.created,
            "reused": bookmark_summary.reused,
            "last_synced_item_id": _resolve_last_synced_item_id(
                previous_bookmark_state,
                bookmark_summary.newest_item_id,
            ),
        },
        TIMELINE_CHANNEL: {
            "status": timeline_summary.status,
            "fetched": timeline_summary.fetched,
            "accepted": timeline_summary.accepted,
            "filtered_out": timeline_summary.filtered_out,
            "errored": timeline_summary.errored,
            "created": timeline_summary.created,
            "reused": timeline_summary.reused,
            "last_synced_item_id": _resolve_last_synced_item_id(
                previous_timeline_state,
                timeline_summary.newest_item_id,
            ),
        },
        LISTS_CHANNEL: {
            "status": lists_summary.status,
            "fetched": lists_summary.fetched,
            "accepted": lists_summary.accepted,
            "filtered_out": lists_summary.filtered_out,
            "errored": lists_summary.errored,
            "created": lists_summary.created,
            "reused": lists_summary.reused,
            "last_synced_item_id": _resolve_last_synced_item_id(
                previous_lists_state,
                lists_summary.newest_item_id,
            ),
            "list_states": _merge_list_state_payload(previous_lists_state, list_state_updates),
        },
    }


def _merge_list_state_payload(
    previous_lists_state: dict[str, Any],
    list_state_updates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    list_states = previous_lists_state.get("list_states")
    merged = list_states.copy() if isinstance(list_states, dict) else {}
    merged.update(list_state_updates)
    return merged


def _resolve_combined_sync_status(
    channel_summaries: dict[str, XSyncChannelSummary],
) -> str:
    statuses = {summary.status for summary in channel_summaries.values()}
    if "failed" in statuses:
        return "failed"
    if "missing_scopes" in statuses or "partial_failure" in statuses:
        return "success_with_warnings"
    return "success"


def _resolve_channel_status(error_count: int) -> str:
    if error_count > 0:
        return "partial_failure"
    return "success"


def _failed_channel_summary() -> XSyncChannelSummary:
    """Return a summary for a channel-level failure without raising."""
    return XSyncChannelSummary(
        status="failed",
        fetched=0,
        accepted=0,
        filtered_out=0,
        errored=1,
        created=0,
        reused=0,
        newest_item_id=None,
    )


def _digest_tweet_content_url(*, user_id: int, tweet_id: str) -> str:
    return f"{canonical_tweet_url(tweet_id)}#newsly-digest-user-{user_id}"


def _clean_optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _ensure_provider_user_id(
    db: Session,
    *,
    user: User,
    connection: UserIntegrationConnection,
    access_token: str,
) -> str:
    provider_user_id = (connection.provider_user_id or "").strip()
    if provider_user_id:
        return provider_user_id

    me = get_authenticated_user(access_token=access_token)
    connection.provider_user_id = me.id
    connection.provider_username = me.username
    if me.username and not user.twitter_username:
        user.twitter_username = normalize_twitter_username(me.username)
    db.commit()
    db.refresh(connection)
    return me.id


def _ensure_valid_access_token(db: Session, connection: UserIntegrationConnection) -> str:
    encrypted_access = connection.access_token_encrypted
    if not encrypted_access:
        raise ValueError("Missing stored X access token")

    now = _now_naive_utc()
    expires_at = connection.token_expires_at
    if not expires_at or expires_at > now + timedelta(seconds=TOKEN_EXPIRY_SKEW_SECONDS):
        return decrypt_token(encrypted_access)

    encrypted_refresh = connection.refresh_token_encrypted
    if not encrypted_refresh:
        raise ValueError("X access token expired and no refresh token is available")

    refresh_token = decrypt_token(encrypted_refresh)
    refreshed = refresh_oauth_token(refresh_token=refresh_token)
    connection.access_token_encrypted = encrypt_token(refreshed.access_token)
    if refreshed.refresh_token:
        connection.refresh_token_encrypted = encrypt_token(refreshed.refresh_token)
    connection.token_expires_at = _expires_at_from_seconds(refreshed.expires_in)
    if refreshed.scopes:
        connection.scopes = refreshed.scopes
    db.commit()
    db.refresh(connection)
    return refreshed.access_token


def _build_pkce_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _pending_state_expired(created_at: Any) -> bool:
    if not isinstance(created_at, str):
        return True
    try:
        parsed = datetime.fromisoformat(created_at)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age_seconds = (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()
    return age_seconds > OAUTH_PENDING_TTL_MINUTES * 60


def _get_connection(db: Session, *, user_id: int) -> UserIntegrationConnection | None:
    return (
        db.query(UserIntegrationConnection)
        .filter(UserIntegrationConnection.user_id == user_id)
        .filter(UserIntegrationConnection.provider == X_PROVIDER)
        .first()
    )


def _get_or_create_connection(db: Session, *, user_id: int) -> UserIntegrationConnection:
    connection = _get_connection(db, user_id=user_id)
    if connection:
        return connection

    connection = UserIntegrationConnection(
        user_id=user_id,
        provider=X_PROVIDER,
        scopes=list(X_DEFAULT_SCOPES),
        is_active=False,
        connection_metadata={},
    )
    db.add(connection)
    db.flush()
    return connection


def _get_sync_state(
    db: Session,
    *,
    connection_id: int,
) -> UserIntegrationSyncState | None:
    return (
        db.query(UserIntegrationSyncState)
        .filter(UserIntegrationSyncState.connection_id == connection_id)
        .first()
    )


def _get_or_create_sync_state(
    db: Session,
    *,
    connection_id: int,
) -> UserIntegrationSyncState:
    sync_state = _get_sync_state(db, connection_id=connection_id)
    if sync_state:
        return sync_state
    sync_state = UserIntegrationSyncState(
        connection_id=connection_id,
        last_status="never_synced",
        sync_metadata={},
    )
    db.add(sync_state)
    db.flush()
    return sync_state


def _normalize_scopes(scopes: Any) -> list[str]:
    if isinstance(scopes, list):
        return [value.strip() for value in scopes if isinstance(value, str) and value.strip()]
    return []


def _expires_at_from_seconds(expires_in: int | None) -> datetime | None:
    if not expires_in or expires_in <= 0:
        return None
    skewed = max(expires_in - TOKEN_EXPIRY_SKEW_SECONDS, 0)
    return _now_naive_utc() + timedelta(seconds=skewed)


def _now_naive_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()
