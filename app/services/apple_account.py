"""Sign in with Apple authorization-code exchange and revocation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

from app.core.settings import get_settings


def _client_secret() -> str:
    settings = get_settings()
    if not settings.apple_team_id or not settings.apple_key_id or not settings.apple_private_key:
        raise RuntimeError("Apple account revocation credentials are not configured")
    now = datetime.now(UTC)
    private_key = settings.apple_private_key.replace("\\n", "\n")
    return jwt.encode(
        {
            "iss": settings.apple_team_id,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "aud": "https://appleid.apple.com",
            "sub": settings.apple_client_id,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": settings.apple_key_id},
    )


def exchange_and_revoke_apple_authorization(authorization_code: str) -> None:
    """Exchange a fresh authorization code and revoke the resulting grant."""
    code = authorization_code.strip()
    if not code:
        raise ValueError("Apple authorization code is required")
    settings = get_settings()
    client_secret = _client_secret()
    with httpx.Client(timeout=20.0) as client:
        token_response = client.post(
            settings.apple_token_url,
            data={
                "client_id": settings.apple_client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        _raise_for_apple_error(token_response, operation="exchange")
        payload: Any = token_response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Apple token exchange returned an invalid response")
        token = payload.get("refresh_token") or payload.get("access_token")
        token_type_hint = "refresh_token" if payload.get("refresh_token") else "access_token"
        if not isinstance(token, str) or not token:
            raise RuntimeError("Apple token exchange did not return a revocable token")
        revoke_response = client.post(
            settings.apple_revoke_url,
            data={
                "client_id": settings.apple_client_id,
                "client_secret": client_secret,
                "token": token,
                "token_type_hint": token_type_hint,
            },
        )
        _raise_for_apple_error(revoke_response, operation="revoke")


def _raise_for_apple_error(response: httpx.Response, *, operation: str) -> None:
    if response.status_code < 400:
        return
    try:
        payload = response.json()
    except Exception:
        payload = response.text[:300]
    raise RuntimeError(f"Apple authorization {operation} failed: {payload}")
