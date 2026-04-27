"""Security utilities for authentication."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.settings import get_settings


def create_token(user_id: int, token_type: str, expires_delta: timedelta) -> str:
    """
    Create a JWT token.

    Args:
        user_id: User ID to encode in token
        token_type: Type of token ('access' or 'refresh')
        expires_delta: Time until token expires

    Returns:
        Encoded JWT token string
    """
    settings = get_settings()

    expire = datetime.now(UTC) + expires_delta
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "exp": expire,
        "iat": datetime.now(UTC),
    }

    encoded_jwt = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


def create_access_token(user_id: int) -> str:
    """
    Create an access token with configured expiry.

    Args:
        user_id: User ID to encode in token

    Returns:
        JWT access token
    """
    settings = get_settings()
    expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return create_token(user_id, "access", expires_delta)


def create_refresh_token(user_id: int) -> str:
    """
    Create a refresh token with configured expiry.

    Args:
        user_id: User ID to encode in token

    Returns:
        JWT refresh token
    """
    settings = get_settings()
    expires_delta = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return create_token(user_id, "refresh", expires_delta)


def verify_token(token: str) -> dict[str, Any]:
    """
    Verify and decode a JWT token.

    Args:
        token: JWT token string

    Returns:
        Decoded token payload

    Raises:
        jwt.ExpiredSignatureError: If token is expired
        jwt.InvalidTokenError: If token is invalid
    """
    settings = get_settings()

    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])

    return payload


def verify_apple_token(id_token: str) -> dict[str, Any]:
    """
    Verify Apple identity token.

    Args:
        id_token: Apple identity token from Sign in with Apple

    Returns:
        Decoded token claims

    Raises:
        ValueError: If token verification fails

    """
    try:
        settings = get_settings()
        signing_key = _get_apple_signing_key(id_token)
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.apple_signin_audiences,
            issuer="https://appleid.apple.com",
        )

        if not claims.get("sub"):
            raise ValueError("Missing subject claim")

        return claims
    except jwt.InvalidTokenError as e:
        raise ValueError(f"Invalid Apple token: {str(e)}") from e


def _get_apple_signing_key(id_token: str) -> Any:
    settings = get_settings()
    jwks_client = jwt.PyJWKClient(settings.apple_jwks_url)
    return jwks_client.get_signing_key_from_jwt(id_token).key


def verify_admin_password(password: str) -> bool:
    """
    Verify admin password against environment variable.

    Args:
        password: Password to verify

    Returns:
        True if password matches, False otherwise
    """
    settings = get_settings()
    return password == settings.ADMIN_PASSWORD


def create_admin_session_token() -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=settings.admin_session_expire_minutes)
    payload = {
        "sub": "admin",
        "type": "admin_session",
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_admin_session_token(token: str) -> dict[str, Any]:
    try:
        payload = verify_token(token)
    except jwt.InvalidTokenError as exc:
        raise ValueError("Invalid admin session") from exc
    if payload.get("type") != "admin_session" or payload.get("sub") != "admin":
        raise ValueError("Invalid admin session")
    return payload
