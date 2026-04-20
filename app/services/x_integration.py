"""Service layer for user-specific X integration state and sync."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import HttpUrl, TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.content_submission import SubmitContentRequest
from app.models.schema import (
    Content,
    UserIntegrationConnection,
    UserIntegrationSyncedItem,
    UserIntegrationSyncState,
)
from app.models.user import User
from app.services.content_submission import submit_user_content
from app.services.token_crypto import decrypt_token, encrypt_token
from app.services.twitter_share import canonical_tweet_url
from app.services.x_api import (
    X_DEFAULT_SCOPES,
    XTweet,
    build_oauth_authorize_url,
    exchange_oauth_code,
    fetch_bookmarks,
    get_authenticated_user,
    refresh_oauth_token,
)
from app.services.x_tweet_metadata import build_tweet_snapshot_metadata

logger = get_logger(__name__)

X_PROVIDER = "x"
OAUTH_PENDING_KEY = "oauth_pending"
OAUTH_PENDING_TTL_MINUTES = 20
TOKEN_EXPIRY_SKEW_SECONDS = 60
BOOKMARK_SYNC_MAX_PAGES = 5
BOOKMARK_SYNC_PAGE_SIZE = 100
BOOKMARKS_CHANNEL = "bookmarks"
USERNAME_REGEX = re.compile(r"^[A-Za-z0-9_]{1,15}$")
UNRECOVERABLE_X_REFRESH_ERROR_MARKERS = (
    "X API 400: invalid_request",
    "X API 400: invalid_grant",
    "X API 400: invalid_client",
    "X API 400: unauthorized_client",
)
URL_ADAPTER = TypeAdapter(HttpUrl)


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


class XReauthRequiredError(ValueError):
    """Raised when a stored X connection can no longer be refreshed."""


def _require_user_id(user: User) -> int:
    user_id = user.id
    if user_id is None:
        raise ValueError("User is missing an id")
    return int(user_id)


def _require_connection_id(connection: UserIntegrationConnection) -> int:
    connection_id = connection.id
    if connection_id is None:
        raise ValueError("User integration connection is missing an id")
    return int(connection_id)


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
    user_id = _require_user_id(user)
    connection = _get_connection(db, user_id=user_id)
    sync_state = (
        _get_sync_state(db, connection_id=_require_connection_id(connection))
        if connection
        else None
    )
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

    connection = _get_or_create_connection(db, user_id=_require_user_id(user))
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
    user_id = _require_user_id(user)
    connection = _get_connection(db, user_id=user_id)
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
    me = get_authenticated_user(
        access_token=token.access_token,
        telemetry={
            "feature": "x_oauth",
            "operation": "x_oauth.get_authenticated_user",
            "user_id": _require_user_id(user),
        },
    )

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

    sync_state = _get_or_create_sync_state(db, connection_id=_require_connection_id(connection))
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
    connection = _get_connection(db, user_id=_require_user_id(user))
    if connection:
        metadata = dict(connection.connection_metadata or {})
        metadata.pop(OAUTH_PENDING_KEY, None)
        metadata["disconnected_at"] = _now_utc_iso()

        connection.is_active = False
        connection.access_token_encrypted = None
        connection.refresh_token_encrypted = None
        connection.token_expires_at = None
        connection.connection_metadata = metadata

        sync_state = _get_or_create_sync_state(db, connection_id=_require_connection_id(connection))
        sync_state.last_status = "disconnected"
        sync_state.last_error = None
        sync_state.last_synced_at = _now_naive_utc()
        db.commit()

    return get_x_connection_view(db, user)


def sync_x_sources_for_user(db: Session, *, user_id: int, force: bool = False) -> XSyncSummary:
    """Sync bookmark-driven X content for a connected user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")

    connection = _get_connection(db, user_id=_require_user_id(user))
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

    sync_state = _get_or_create_sync_state(db, connection_id=_require_connection_id(connection))
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

    try:
        access_token = _ensure_valid_access_token(db, connection)
        provider_user_id = _ensure_provider_user_id(
            db,
            user=user,
            connection=connection,
            access_token=access_token,
        )
        bookmark_state = _get_channel_state(existing_sync_metadata, BOOKMARKS_CHANNEL)
        if _should_skip_channel_sync(
            bookmark_state,
            min_interval_minutes=get_settings().x_bookmark_sync_min_interval_minutes,
        ):
            bookmark_summary = _skipped_channel_summary()
        else:
            bookmark_summary = _sync_bookmark_channel(
                db,
                user=user,
                connection_id=_require_connection_id(connection),
                access_token=access_token,
                provider_user_id=provider_user_id,
                existing_sync_metadata=existing_sync_metadata,
            )

        channel_summaries = {
            BOOKMARKS_CHANNEL: bookmark_summary,
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
        sync_state.last_error = None
        if bookmark_summary.newest_item_id:
            sync_state.last_synced_item_id = bookmark_summary.newest_item_id
        sync_state.cursor = None
        sync_state.sync_metadata = _build_sync_metadata_payload(
            existing_sync_metadata=existing_sync_metadata,
            bookmark_summary=bookmark_summary,
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

    except XReauthRequiredError as exc:
        sync_state.last_status = "reauth_required"
        sync_state.last_error = str(exc)[:2000]
        db.commit()
        return XSyncSummary(
            status="reauth_required",
            fetched=0,
            accepted=0,
            filtered_out=0,
            errored=0,
            created=0,
            reused=0,
            channels={},
        )
    except Exception as exc:  # noqa: BLE001
        sync_state.last_status = "failed"
        sync_state.last_error = str(exc)[:2000]
        db.commit()
        raise


def sync_x_bookmarks_for_user(db: Session, *, user_id: int) -> XSyncSummary:
    """Backward-compatible wrapper for bookmark-first X sync."""
    return sync_x_sources_for_user(db, user_id=user_id)


def _sync_bookmark_channel(
    db: Session,
    *,
    user: User,
    connection_id: int,
    access_token: str,
    provider_user_id: str,
    existing_sync_metadata: dict[str, Any],
) -> XSyncChannelSummary:
    bookmark_state = _get_channel_state(existing_sync_metadata, BOOKMARKS_CHANNEL)
    last_synced_id = _clean_optional_string(bookmark_state.get("last_synced_item_id"))
    user_id = _require_user_id(user)
    newest_seen_id: str | None = None
    fetched = 0
    collected_new: list[XTweet] = []
    included_tweets: dict[str, XTweet] = {}
    next_token: str | None = None
    reached_previous_sync = False

    for _ in range(BOOKMARK_SYNC_MAX_PAGES):
        page = fetch_bookmarks(
            access_token=access_token,
            user_id=provider_user_id,
            pagination_token=next_token,
            max_results=BOOKMARK_SYNC_PAGE_SIZE,
            telemetry={
                "feature": "x_sync",
                "operation": "x_sync.bookmarks.read",
                "user_id": user_id,
                "metadata": {"channel": BOOKMARKS_CHANNEL},
            },
        )
        included_tweets.update(page.included_tweets)
        if page.tweets and newest_seen_id is None:
            newest_seen_id = page.tweets[0].id

        fetched += len(page.tweets)
        if not page.tweets:
            break

        for tweet in page.tweets:
            if last_synced_id and tweet.id == last_synced_id:
                reached_previous_sync = True
                break
            collected_new.append(tweet)

        if reached_previous_sync or not page.next_token:
            break
        next_token = page.next_token

    created = 0
    reused = 0
    synced_items_by_external_id = _load_synced_items_by_external_id(
        db,
        connection_id=connection_id,
        channel=BOOKMARKS_CHANNEL,
        external_item_ids=[tweet.id for tweet in collected_new],
    )
    synced_content_ids = {
        int(synced_item.content_id)
        for synced_item in synced_items_by_external_id.values()
        if synced_item.content_id is not None
    }
    reusable_content_ids = (
        {
            int(content_id)
            for (content_id,) in db.query(Content.id)
            .filter(Content.id.in_(synced_content_ids))
            .all()
        }
        if synced_content_ids
        else set()
    )
    for tweet in reversed(collected_new):
        tweet_url = str(URL_ADAPTER.validate_python(canonical_tweet_url(tweet.id)))
        existing_synced_item = synced_items_by_external_id.get(tweet.id)
        existing_content_id = (
            int(existing_synced_item.content_id)
            if existing_synced_item is not None
            and existing_synced_item.content_id in reusable_content_ids
            else None
        )
        if existing_content_id is not None:
            _persist_bookmark_tweet_snapshot(
                db,
                content_id=existing_content_id,
                tweet=tweet,
                included_tweets=included_tweets,
            )
            synced_items_by_external_id[tweet.id] = _upsert_synced_item(
                db,
                synced_item=existing_synced_item,
                connection_id=connection_id,
                channel=BOOKMARKS_CHANNEL,
                external_item_id=tweet.id,
                content_id=existing_content_id,
                item_url=tweet_url,
            )
            reused += 1
            continue

        result = submit_user_content(
            db,
            SubmitContentRequest(
                url=URL_ADAPTER.validate_python(tweet_url),
                content_type=None,
                title=None,
                platform="twitter",
                instruction=None,
                crawl_links=False,
                subscribe_to_feed=False,
                share_and_chat=False,
                save_to_knowledge_and_mark_read=False,
            ),
            user,
            submitted_via="x_bookmarks",
        )
        _persist_bookmark_tweet_snapshot(
            db,
            content_id=result.content_id,
            tweet=tweet,
            included_tweets=included_tweets,
        )
        synced_items_by_external_id[tweet.id] = _upsert_synced_item(
            db,
            synced_item=existing_synced_item,
            connection_id=connection_id,
            channel=BOOKMARKS_CHANNEL,
            external_item_id=tweet.id,
            content_id=result.content_id,
            item_url=tweet_url,
        )
        if result.already_exists:
            reused += 1
        else:
            created += 1
    if collected_new:
        db.commit()

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


def _persist_bookmark_tweet_snapshot(
    db: Session,
    *,
    content_id: int,
    tweet: XTweet,
    included_tweets: dict[str, XTweet],
) -> None:
    content = db.query(Content).filter(Content.id == content_id).first()
    if content is None:
        return
    existing_metadata = (
        content.content_metadata if isinstance(content.content_metadata, dict) else {}
    )
    linked_tweet_ids = set(tweet.linked_tweet_ids)
    content.content_metadata = {
        **existing_metadata,
        **build_tweet_snapshot_metadata(
            tweet=tweet,
            included_tweets={
                tweet_id: included_tweet
                for tweet_id, included_tweet in included_tweets.items()
                if tweet_id in linked_tweet_ids
            },
            snapshot_source="x_bookmarks_sync",
        ),
    }


def _load_synced_items_by_external_id(
    db: Session,
    *,
    connection_id: int,
    channel: str,
    external_item_ids: list[str],
) -> dict[str, UserIntegrationSyncedItem]:
    cleaned_ids = sorted({item_id.strip() for item_id in external_item_ids if item_id.strip()})
    if not cleaned_ids:
        return {}

    rows = (
        db.query(UserIntegrationSyncedItem)
        .filter(UserIntegrationSyncedItem.connection_id == connection_id)
        .filter(UserIntegrationSyncedItem.channel == channel)
        .filter(UserIntegrationSyncedItem.external_item_id.in_(cleaned_ids))
        .all()
    )
    return {row.external_item_id: row for row in rows if row.external_item_id is not None}


def _upsert_synced_item(
    db: Session,
    *,
    synced_item: UserIntegrationSyncedItem | None,
    connection_id: int,
    channel: str,
    external_item_id: str,
    content_id: int,
    item_url: str,
) -> UserIntegrationSyncedItem:
    now = _now_naive_utc()
    if synced_item is None:
        synced_item = UserIntegrationSyncedItem(
            connection_id=connection_id,
            channel=channel,
            external_item_id=external_item_id,
            content_id=content_id,
            item_url=item_url,
            first_synced_at=now,
            last_seen_at=now,
        )
        db.add(synced_item)
        return synced_item
    synced_item.content_id = content_id
    synced_item.item_url = item_url
    synced_item.last_seen_at = now
    return synced_item


def _get_channel_state(sync_metadata: dict[str, Any], channel: str) -> dict[str, Any]:
    state = sync_metadata.get(channel)
    return state if isinstance(state, dict) else {}


def _parse_channel_last_synced_at(state: dict[str, Any]) -> datetime | None:
    raw_value = state.get("last_synced_at")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _should_skip_scheduled_sync(sync_state: UserIntegrationSyncState) -> bool:
    last_synced_at = sync_state.last_synced_at
    if last_synced_at is None:
        return False
    min_interval_minutes = get_settings().x_sync_min_interval_minutes
    elapsed_seconds = (_now_naive_utc() - last_synced_at).total_seconds()
    return elapsed_seconds < min_interval_minutes * 60


def _should_skip_channel_sync(
    previous_state: dict[str, Any],
    *,
    min_interval_minutes: int,
) -> bool:
    last_synced_at = _parse_channel_last_synced_at(previous_state)
    if last_synced_at is None:
        return False
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
) -> dict[str, Any]:
    previous_bookmark_state = _get_channel_state(existing_sync_metadata, BOOKMARKS_CHANNEL)
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
            "last_synced_at": _resolve_channel_last_synced_at(
                previous_bookmark_state,
                bookmark_summary.status,
            ),
        },
    }


def _resolve_channel_last_synced_at(
    previous_state: dict[str, Any],
    status: str,
) -> str | None:
    if status == "skipped_recently":
        previous_value = previous_state.get("last_synced_at")
        if isinstance(previous_value, str) and previous_value.strip():
            return previous_value
        return None
    return _now_utc_iso()


def _resolve_combined_sync_status(
    channel_summaries: dict[str, XSyncChannelSummary],
) -> str:
    statuses = {summary.status for summary in channel_summaries.values()}
    if "failed" in statuses:
        return "failed"
    if "missing_scopes" in statuses or "partial_failure" in statuses:
        return "success_with_warnings"
    return "success"


def _skipped_channel_summary() -> XSyncChannelSummary:
    return XSyncChannelSummary(
        status="skipped_recently",
        fetched=0,
        accepted=0,
        filtered_out=0,
        errored=0,
        created=0,
        reused=0,
        newest_item_id=None,
    )


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

    me = get_authenticated_user(
        access_token=access_token,
        telemetry={
            "feature": "x_sync",
            "operation": "x_sync.ensure_provider_user",
            "user_id": _require_user_id(user),
        },
    )
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
    try:
        refreshed = refresh_oauth_token(refresh_token=refresh_token)
    except Exception as exc:  # noqa: BLE001
        if not _is_unrecoverable_refresh_error(exc):
            raise
        _deactivate_connection_for_reauth(
            db,
            connection=connection,
            reason=str(exc),
        )
        raise XReauthRequiredError(
            "X connection requires reauthentication after token refresh failed"
        ) from exc
    connection.access_token_encrypted = encrypt_token(refreshed.access_token)
    if refreshed.refresh_token:
        connection.refresh_token_encrypted = encrypt_token(refreshed.refresh_token)
    connection.token_expires_at = _expires_at_from_seconds(refreshed.expires_in)
    if refreshed.scopes:
        connection.scopes = refreshed.scopes
    db.commit()
    db.refresh(connection)
    return refreshed.access_token


def _is_unrecoverable_refresh_error(exc: Exception) -> bool:
    message = str(exc)
    return any(marker in message for marker in UNRECOVERABLE_X_REFRESH_ERROR_MARKERS)


def _deactivate_connection_for_reauth(
    db: Session,
    *,
    connection: UserIntegrationConnection,
    reason: str,
) -> None:
    metadata = (
        dict(connection.connection_metadata)
        if isinstance(connection.connection_metadata, dict)
        else {}
    )
    metadata["reauth_required"] = {
        "reason": reason[:1000],
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    connection.connection_metadata = metadata
    connection.is_active = False
    connection.access_token_encrypted = None
    connection.refresh_token_encrypted = None
    connection.token_expires_at = None
    db.commit()
    db.refresh(connection)


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
