"""Learning Deck private and public share token handling."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.db import LearningDeck
from app.services.learning_deck_common import (
    LearningDeckError,
    LearningDeckSignedToken,
    require_int_value,
    utcnow,
)

SIGNED_TOKEN_TYPE = "learning_deck_signed"
SHARE_TOKEN_TYPE = "learning_deck_share"


def build_private_learning_deck_token(*, deck: LearningDeck, user_id: int) -> tuple[str, datetime]:
    """Build a short-lived private viewer token."""
    settings = get_settings()
    expires_at = datetime.now(UTC) + timedelta(
        seconds=settings.learning_deck_signed_url_ttl_seconds
    )
    deck_id = require_int_value(deck.id, "Learning Deck id")
    token = jwt.encode(
        {
            "type": SIGNED_TOKEN_TYPE,
            "deck_id": deck_id,
            "user_id": user_id,
            "exp": expires_at,
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return token, expires_at


def decode_private_learning_deck_token(token: str) -> LearningDeckSignedToken:
    """Decode and validate a short-lived private viewer token."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise LearningDeckError("Invalid or expired Learning Deck URL", status_code=403) from exc
    if payload.get("type") != SIGNED_TOKEN_TYPE:
        raise LearningDeckError("Invalid Learning Deck URL", status_code=403)
    try:
        deck_id = int(payload["deck_id"])
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LearningDeckError("Invalid Learning Deck URL", status_code=403) from exc
    return LearningDeckSignedToken(deck_id=deck_id, user_id=user_id)


def get_deck_by_private_token(db: Session, *, token: str) -> LearningDeck:
    """Return a deck addressed by a short-lived private token."""
    decoded = decode_private_learning_deck_token(token)
    deck = (
        db.query(LearningDeck)
        .filter(
            LearningDeck.id == decoded.deck_id,
            LearningDeck.user_id == decoded.user_id,
            LearningDeck.deleted_at.is_(None),
        )
        .first()
    )
    if deck is None or not deck.latest_successful_run_id:
        raise LearningDeckError("Learning Deck is not available", status_code=404)
    return deck


def enable_learning_deck_share(db: Session, *, user_id: int, deck_id: int) -> str:
    """Enable sharing for a deck and return its stable share token."""
    deck = _get_owned_deck(db, user_id=user_id, deck_id=deck_id)
    if deck is None:
        raise LearningDeckError("Learning Deck not found", status_code=404)
    if not deck.latest_successful_run_id:
        raise LearningDeckError("Learning Deck is not ready to share", status_code=409)
    if not deck.share_token_nonce:
        deck.share_token_nonce = secrets.token_urlsafe(24)
    token = _encode_share_token(deck_id=deck_id, nonce=str(deck.share_token_nonce))
    deck.share_token_hash = _hash_token(token)
    deck.share_enabled = True
    deck.updated_at = utcnow()
    db.commit()
    return token


def disable_learning_deck_share(db: Session, *, user_id: int, deck_id: int) -> None:
    """Disable public sharing for a deck."""
    deck = _get_owned_deck(db, user_id=user_id, deck_id=deck_id)
    if deck is None:
        raise LearningDeckError("Learning Deck not found", status_code=404)
    deck.share_enabled = False
    deck.updated_at = utcnow()
    db.commit()


def get_deck_by_valid_share_token(db: Session, *, token: str) -> LearningDeck:
    """Return a shared deck for a durable public token."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError as exc:
        raise LearningDeckError("Invalid share link", status_code=404) from exc
    if payload.get("type") != SHARE_TOKEN_TYPE:
        raise LearningDeckError("Invalid share link", status_code=404)
    try:
        deck_id = int(payload["deck_id"])
        nonce = str(payload["nonce"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LearningDeckError("Invalid share link", status_code=404) from exc
    deck = (
        db.query(LearningDeck)
        .filter(LearningDeck.id == deck_id, LearningDeck.deleted_at.is_(None))
        .first()
    )
    if (
        deck is None
        or not deck.share_enabled
        or not deck.latest_successful_run_id
        or not deck.share_token_nonce
        or not deck.share_token_hash
        or not hmac.compare_digest(str(deck.share_token_nonce), nonce)
        or not hmac.compare_digest(str(deck.share_token_hash), _hash_token(token))
    ):
        raise LearningDeckError("Share link is not available", status_code=404)
    return deck


def _get_owned_deck(db: Session, *, user_id: int, deck_id: int) -> LearningDeck | None:
    return (
        db.query(LearningDeck)
        .filter(
            LearningDeck.id == deck_id,
            LearningDeck.user_id == user_id,
            LearningDeck.deleted_at.is_(None),
        )
        .first()
    )


def _encode_share_token(*, deck_id: int, nonce: str) -> str:
    settings = get_settings()
    return jwt.encode(
        {"type": SHARE_TOKEN_TYPE, "deck_id": deck_id, "nonce": nonce},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
