"""Repository helpers for user-managed integrations, including LLM keys."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.schema import UserIntegrationConnection
from app.services.token_crypto import decrypt_token, encrypt_token

SUPPORTED_LLM_PROVIDERS = ("anthropic", "openai", "google")


def list_user_llm_integrations(db: Session, *, user_id: int) -> list[UserIntegrationConnection]:
    """List user-managed LLM provider credentials."""
    return list(
        db.query(UserIntegrationConnection)
        .filter(UserIntegrationConnection.user_id == user_id)
        .filter(UserIntegrationConnection.provider.in_(SUPPORTED_LLM_PROVIDERS))
        .order_by(UserIntegrationConnection.provider.asc())
        .all()
    )


def upsert_user_llm_integration(
    db: Session,
    *,
    user_id: int,
    provider: str,
    api_key: str,
) -> UserIntegrationConnection:
    """Create or update a user-managed LLM provider key."""
    record = (
        db.query(UserIntegrationConnection)
        .filter(UserIntegrationConnection.user_id == user_id)
        .filter(UserIntegrationConnection.provider == provider)
        .first()
    )
    encrypted = encrypt_token(api_key)
    if record is None:
        record = UserIntegrationConnection(
            user_id=user_id,
            provider=provider,
            access_token_encrypted=encrypted,
            is_active=True,
            connection_metadata={"kind": "llm_api_key"},
        )
        db.add(record)
    else:
        record.access_token_encrypted = encrypted
        record.is_active = True
        metadata = dict(record.connection_metadata or {})
        metadata["kind"] = "llm_api_key"
        record.connection_metadata = metadata
    db.commit()
    db.refresh(record)
    return record


def delete_user_llm_integration(db: Session, *, user_id: int, provider: str) -> bool:
    """Delete a user-managed LLM provider key."""
    record = (
        db.query(UserIntegrationConnection)
        .filter(UserIntegrationConnection.user_id == user_id)
        .filter(UserIntegrationConnection.provider == provider)
        .first()
    )
    if record is None:
        return False
    db.delete(record)
    db.commit()
    return True


def get_user_llm_api_key(db: Session, *, user_id: int, provider: str) -> str | None:
    """Return a decrypted user-managed API key for the provider when present."""
    record = (
        db.query(UserIntegrationConnection)
        .filter(UserIntegrationConnection.user_id == user_id)
        .filter(UserIntegrationConnection.provider == provider)
        .filter(UserIntegrationConnection.is_active == True)  # noqa: E712
        .first()
    )
    if record is None or not record.access_token_encrypted:
        return None
    return decrypt_token(record.access_token_encrypted)
