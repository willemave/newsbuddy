"""Tests for security utilities."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.security import (
    create_access_token,
    create_admin_session_token,
    create_refresh_token,
    verify_admin_session_token,
    verify_apple_token,
    verify_token,
)
from app.core.settings import get_settings

TEST_JWT_KEY = "dummy-key-for-tests-only-32-bytes"


def test_create_access_token():
    """Test access token creation."""
    user_id = 123
    token = create_access_token(user_id)

    assert isinstance(token, str)
    assert len(token) > 0

    # Decode and verify
    settings = get_settings()
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])

    assert payload["sub"] == str(user_id)
    assert payload["type"] == "access"
    assert "exp" in payload


def test_create_refresh_token():
    """Test refresh token creation."""
    user_id = 456
    token = create_refresh_token(user_id)

    assert isinstance(token, str)
    assert len(token) > 0

    # Decode and verify
    settings = get_settings()
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])

    assert payload["sub"] == str(user_id)
    assert payload["type"] == "refresh"


def test_verify_token_valid():
    """Test token verification with valid token."""
    user_id = 789
    token = create_access_token(user_id)

    payload = verify_token(token)

    assert payload["sub"] == str(user_id)
    assert payload["type"] == "access"


def test_verify_token_expired():
    """Test token verification with expired token."""
    from app.core.security import create_token

    # Create token that expired 1 hour ago
    user_id = 999
    token = create_token(user_id, "access", timedelta(hours=-1))

    with pytest.raises(jwt.ExpiredSignatureError):
        verify_token(token)


def test_verify_token_invalid():
    """Test token verification with invalid token."""
    with pytest.raises(jwt.InvalidTokenError):
        verify_token("invalid.token.here")


@pytest.fixture
def apple_private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_verify_apple_token_valid_signature(monkeypatch, apple_private_key):
    """Apple tokens must validate against the signing key, issuer, audience, and subject."""
    mock_apple_claims = {
        "iss": "https://appleid.apple.com",
        "aud": "org.willemaw.newsly",
        "sub": "001234.abcdef123456.7890",
        "email": "test@icloud.com",
        "email_verified": True,
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "iat": datetime.now(UTC),
    }
    test_token = jwt.encode(
        mock_apple_claims,
        apple_private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    monkeypatch.setattr(
        "app.core.security._get_apple_signing_key",
        lambda _token: apple_private_key.public_key(),
    )

    claims = verify_apple_token(test_token)

    assert claims["sub"] == "001234.abcdef123456.7890"
    assert claims["email"] == "test@icloud.com"
    assert claims["iss"] == "https://appleid.apple.com"


def test_verify_apple_token_missing_required_claims(monkeypatch, apple_private_key):
    """Test that verify_apple_token validates required claims."""
    invalid_claims = {
        "sub": "001234.test",
        "aud": "org.willemaw.newsly",
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "iat": datetime.now(UTC),
    }
    test_token = jwt.encode(
        invalid_claims,
        apple_private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    monkeypatch.setattr(
        "app.core.security._get_apple_signing_key",
        lambda _token: apple_private_key.public_key(),
    )

    with pytest.raises(ValueError, match="Invalid Apple token"):
        verify_apple_token(test_token)


def test_verify_apple_token_rejects_wrong_signature(apple_private_key):
    claims = {
        "iss": "https://appleid.apple.com",
        "aud": "org.willemaw.newsly",
        "sub": "001234.test",
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "iat": datetime.now(UTC),
    }
    test_token = jwt.encode(
        claims,
        apple_private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "app.core.security._get_apple_signing_key",
            lambda _token: other_key.public_key(),
        )
        with pytest.raises(ValueError, match="Invalid Apple token"):
            verify_apple_token(test_token)


def test_verify_admin_session_token_valid():
    token = create_admin_session_token()
    payload = verify_admin_session_token(token)
    assert payload["sub"] == "admin"
    assert payload["type"] == "admin_session"


def test_verify_admin_session_token_rejects_access_token():
    token = create_access_token(123)
    with pytest.raises(ValueError, match="Invalid admin session"):
        verify_admin_session_token(token)
