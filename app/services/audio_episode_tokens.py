"""Audio episode public share token handling."""

from __future__ import annotations

import hashlib
import hmac
import secrets

import jwt
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.db import AudioEpisode
from app.services.audio_episode_kinds import CUSTOM_NARRATION_KIND

SHARE_TOKEN_TYPE = "audio_episode_share"


class AudioEpisodeShareError(RuntimeError):
    """Public share link error with an HTTP-friendly status code."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def enable_audio_episode_share(
    db: Session,
    *,
    user_id: int,
    audio_episode_id: int,
) -> str:
    """Enable public sharing for a completed custom narration and return its token."""

    episode = _get_owned_episode(db, user_id=user_id, audio_episode_id=audio_episode_id)
    _require_shareable_episode(episode)
    if not episode.share_token_nonce:
        episode.share_token_nonce = secrets.token_urlsafe(24)
    token = _encode_share_token(
        audio_episode_id=audio_episode_id,
        nonce=str(episode.share_token_nonce),
    )
    episode.share_token_hash = _hash_token(token)
    episode.share_enabled = True
    db.commit()
    return token


def disable_audio_episode_share(
    db: Session,
    *,
    user_id: int,
    audio_episode_id: int,
) -> None:
    """Disable public sharing for an audio episode."""

    episode = _get_owned_episode(db, user_id=user_id, audio_episode_id=audio_episode_id)
    episode.share_enabled = False
    episode.share_token_hash = None
    episode.share_token_nonce = None
    db.commit()


def get_audio_episode_by_valid_share_token(db: Session, *, token: str) -> AudioEpisode:
    """Return a shared audio episode for a durable public token."""

    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError as exc:
        raise AudioEpisodeShareError("Invalid share link", status_code=404) from exc
    if payload.get("type") != SHARE_TOKEN_TYPE:
        raise AudioEpisodeShareError("Invalid share link", status_code=404)
    try:
        audio_episode_id = int(payload["audio_episode_id"])
        nonce = str(payload["nonce"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AudioEpisodeShareError("Invalid share link", status_code=404) from exc

    episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).first()
    if (
        episode is None
        or not episode.share_enabled
        or not episode.share_token_nonce
        or not episode.share_token_hash
        or not hmac.compare_digest(str(episode.share_token_nonce), nonce)
        or not hmac.compare_digest(str(episode.share_token_hash), _hash_token(token))
    ):
        raise AudioEpisodeShareError("Share link is not available", status_code=404)
    _require_shareable_episode(episode)
    return episode


def _get_owned_episode(
    db: Session,
    *,
    user_id: int,
    audio_episode_id: int,
) -> AudioEpisode:
    episode = (
        db.query(AudioEpisode)
        .filter(AudioEpisode.id == audio_episode_id, AudioEpisode.user_id == user_id)
        .first()
    )
    if episode is None:
        raise AudioEpisodeShareError("Audio episode not found", status_code=404)
    return episode


def _require_shareable_episode(episode: AudioEpisode) -> None:
    if episode.kind != CUSTOM_NARRATION_KIND:
        raise AudioEpisodeShareError("Only custom narrations can be shared", status_code=400)
    if episode.status != "completed" or not episode.audio_storage_path:
        raise AudioEpisodeShareError("Narration is not ready to share", status_code=409)


def _encode_share_token(*, audio_episode_id: int, nonce: str) -> str:
    settings = get_settings()
    return jwt.encode(
        {
            "type": SHARE_TOKEN_TYPE,
            "audio_episode_id": audio_episode_id,
            "nonce": nonce,
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
