"""OAuth lifecycle helpers for the official X API."""

from __future__ import annotations

from app.core.settings import get_settings


def revoke_oauth_token(*, token: str, token_type_hint: str) -> None:
    """Revoke an X OAuth access or refresh token."""
    normalized_token = token.strip()
    if not normalized_token:
        raise ValueError("OAuth token is required")
    if token_type_hint not in {"access_token", "refresh_token"}:
        raise ValueError("Unsupported OAuth token type hint")

    settings = get_settings()
    client_id = (settings.x_client_id or "").strip()
    if not client_id:
        raise ValueError("X OAuth is not configured (X_CLIENT_ID is required)")
    client_secret = (settings.x_client_secret or "").strip()
    auth = (client_id, client_secret) if client_secret else None
    revoke_url = settings.x_oauth_token_url.removesuffix("/token") + "/revoke"

    # Import at call time so the established x_api request seam remains patchable in tests.
    from app.services.x_api import _request_json

    _request_json(
        "POST",
        revoke_url,
        access_token=None,
        data={
            "token": normalized_token,
            "token_type_hint": token_type_hint,
            "client_id": client_id,
        },
        auth=auth,
    )
