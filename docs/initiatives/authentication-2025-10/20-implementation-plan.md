# Authentication System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Apple Sign In authentication for iOS users and admin password protection for web routes

**Architecture:** JWT-based stateless auth for iOS API (PyJWT + authlib for Apple token verification), session-based auth for web admin. Replace session_id with user_id in favorites/read-status tables.

**Tech Stack:** FastAPI, PyJWT, authlib, SQLAlchemy, Alembic, SwiftUI, AuthenticationServices, Keychain

---

## Phase 1: Backend Infrastructure

### Task 1: Add Authentication Dependencies

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add PyJWT and authlib dependencies**

```bash
uv add PyJWT authlib
```

Expected output: Dependencies added to pyproject.toml and uv.lock updated

**Step 2: Verify installation**

```bash
source .venv/bin/activate && python -c "import jwt; import authlib; print('Dependencies installed')"
```

Expected: "Dependencies installed"

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add PyJWT and authlib for authentication"
```

---

### Task 2: Add Auth Configuration to Settings

**Files:**
- Modify: `app/core/settings.py`
- Modify: `.env.example`

**Step 1: Add auth settings to Settings class**

In `app/core/settings.py`, add these fields to the `Settings` class (around line 20):

```python
# Authentication settings
JWT_SECRET_KEY: str = Field(..., description="Secret key for JWT token signing")
JWT_ALGORITHM: str = Field(default="HS256", description="JWT signing algorithm")
ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, description="Access token expiry in minutes")
REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, description="Refresh token expiry in days")
ADMIN_PASSWORD: str = Field(..., description="Admin password for web access")
```

**Step 2: Update .env.example**

Add to `.env.example`:

```bash
# Authentication
JWT_SECRET_KEY=your-secret-key-here-change-in-production
ADMIN_PASSWORD=your-admin-password-here
```

**Step 3: Update your local .env file**

```bash
echo "" >> .env
echo "# Authentication" >> .env
echo "JWT_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env
echo "ADMIN_PASSWORD=admin123" >> .env
```

**Step 4: Test settings load**

```bash
source .venv/bin/activate && python -c "from app.core.settings import get_settings; s = get_settings(); print(f'JWT_SECRET_KEY: {s.JWT_SECRET_KEY[:10]}...')"
```

Expected: Should print truncated secret key without errors

**Step 5: Commit**

```bash
git add app/core/settings.py .env.example
git commit -m "feat: add authentication settings to config"
```

---

### Task 3: Create User Model and Schemas

**Files:**
- Create: `app/models/user.py`

**Step 1: Create User SQLAlchemy model and Pydantic schemas**

Create `app/models/user.py`:

```python
"""User models and schemas for authentication."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.core.db import Base


class User(Base):
    """User account model."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    apple_id = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=True)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# Pydantic schemas
class UserBase(BaseModel):
    """Base user schema."""

    email: EmailStr
    full_name: Optional[str] = None


class UserCreate(UserBase):
    """Schema for creating a user."""

    apple_id: str


class UserResponse(UserBase):
    """Schema for user API responses."""

    id: int
    apple_id: str
    is_admin: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AppleSignInRequest(BaseModel):
    """Request schema for Apple Sign In."""

    id_token: str = Field(..., description="Apple identity token")
    email: EmailStr
    full_name: Optional[str] = None


class TokenResponse(BaseModel):
    """Response schema for authentication tokens."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse
```

**Step 2: Import User model in schema.py**

Add to `app/models/schema.py` (near top with other imports):

```python
from app.models.user import User  # noqa: F401
```

**Step 3: Commit**

```bash
git add app/models/user.py app/models/schema.py
git commit -m "feat: add User model and authentication schemas"
```

---

### Task 4: Create Database Migration for Users Table

**Files:**
- Create: `alembic/versions/YYYYMMDD_create_users_table.py` (autogenerated)

**Step 1: Generate migration**

```bash
source .venv/bin/activate && alembic revision --autogenerate -m "create users table"
```

Expected: Creates new migration file in `alembic/versions/`

**Step 2: Review the generated migration**

```bash
ls -la alembic/versions/ | tail -1
```

Open the newest file and verify it includes:
- `users` table creation
- Columns: id, apple_id, email, full_name, is_admin, is_active, created_at, updated_at
- Indexes on apple_id and email
- Unique constraints on apple_id and email

**Step 3: Apply migration**

```bash
source .venv/bin/activate && alembic upgrade head
```

Expected: "Running upgrade ... -> ..., create users table"

**Step 4: Verify table exists**

```bash
source .venv/bin/activate && python -c "from app.core.db import engine; from sqlalchemy import inspect; print('users' in inspect(engine).get_table_names())"
```

Expected: True

**Step 5: Commit**

```bash
git add alembic/versions/*create_users_table.py
git commit -m "db: create users table migration"
```

---

### Task 5: Create Security Utilities Module

**Files:**
- Create: `app/core/security.py`
- Create: `app/tests/core/test_security.py`

**Step 1: Write test for JWT token creation**

Create `app/tests/core/test_security.py`:

```python
"""Tests for security utilities."""
from datetime import datetime, timedelta

import jwt
import pytest

from app.core.security import create_access_token, create_refresh_token, verify_token
from app.core.settings import get_settings


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
```

**Step 2: Run test to verify it fails**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/core/test_security.py -v
```

Expected: FAIL - ModuleNotFoundError: No module named 'app.core.security'

**Step 3: Implement security utilities**

Create `app/core/security.py`:

```python
"""Security utilities for authentication."""
from datetime import datetime, timedelta
from typing import Any, Dict

import jwt
from authlib.jose import JsonWebToken, JWTClaims
from authlib.jose.errors import JoseError

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

    expire = datetime.utcnow() + expires_delta
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "exp": expire,
        "iat": datetime.utcnow(),
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


def verify_token(token: str) -> Dict[str, Any]:
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

    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM]
    )

    return payload


def verify_apple_token(id_token: str) -> Dict[str, Any]:
    """
    Verify Apple identity token.

    Args:
        id_token: Apple identity token from Sign in with Apple

    Returns:
        Decoded token claims

    Raises:
        JoseError: If token verification fails

    Note:
        In production, this should fetch Apple's public keys and verify signature.
        For MVP, we'll do basic decoding without signature verification.
        TODO: Implement full verification with Apple's public keys
    """
    try:
        # For MVP: decode without verification (ONLY for development)
        # Production TODO: Verify signature with Apple's public keys
        jwt_instance = JsonWebToken(['RS256'])
        claims = jwt_instance.decode(id_token, None, claims_options={
            "iss": {"essential": True, "value": "https://appleid.apple.com"},
            "aud": {"essential": True},  # Should match your app's bundle ID
        })

        return dict(claims)
    except JoseError as e:
        raise ValueError(f"Invalid Apple token: {str(e)}")


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
```

**Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/core/test_security.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add app/core/security.py app/tests/core/test_security.py
git commit -m "feat: add JWT token creation and verification utilities"
```

---

### Task 6: Create Authentication Dependencies

**Files:**
- Create: `app/core/deps.py`
- Create: `app/tests/core/test_deps.py`

**Step 1: Write test for get_current_user dependency**

Create `app/tests/core/test_deps.py`:

```python
"""Tests for FastAPI dependencies."""
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.security import create_access_token, create_refresh_token
from app.models.user import User


def test_get_current_user_valid_token(db: Session):
    """Test get_current_user with valid token."""
    # Create test user
    user = User(
        apple_id="test.apple.001",
        email="test@example.com",
        full_name="Test User",
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Create valid access token
    token = create_access_token(user.id)

    # Get user from token
    result = get_current_user(token=token, db=db)

    assert result.id == user.id
    assert result.email == user.email


def test_get_current_user_invalid_token(db: Session):
    """Test get_current_user with invalid token."""
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token="invalid.token.here", db=db)

    assert exc_info.value.status_code == 401
    assert "Could not validate credentials" in str(exc_info.value.detail)


def test_get_current_user_nonexistent_user(db: Session):
    """Test get_current_user with token for non-existent user."""
    # Create token for user ID that doesn't exist
    token = create_access_token(999999)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token=token, db=db)

    assert exc_info.value.status_code == 401


def test_get_current_user_inactive_user(db: Session):
    """Test get_current_user with inactive user."""
    # Create inactive user
    user = User(
        apple_id="test.apple.002",
        email="inactive@example.com",
        full_name="Inactive User",
        is_active=False
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token=token, db=db)

    assert exc_info.value.status_code == 400
    assert "Inactive user" in str(exc_info.value.detail)


def test_get_current_user_refresh_token(db: Session):
    """Test get_current_user rejects refresh token."""
    # Create user
    user = User(
        apple_id="test.apple.003",
        email="test3@example.com",
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Try with refresh token (should fail)
    refresh_token = create_refresh_token(user.id)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token=refresh_token, db=db)

    assert exc_info.value.status_code == 401
```

**Step 2: Run test to verify it fails**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/core/test_deps.py::test_get_current_user_valid_token -v
```

Expected: FAIL - ModuleNotFoundError or ImportError

**Step 3: Implement authentication dependencies**

Create `app/core/deps.py`:

```python
"""FastAPI dependencies for authentication and authorization."""
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import verify_token
from app.models.user import User

# HTTP Bearer token scheme for JWT authentication
security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Get current authenticated user from JWT token.

    Args:
        credentials: HTTP Bearer credentials from Authorization header
        db: Database session

    Returns:
        Current authenticated user

    Raises:
        HTTPException: 401 if token is invalid or user not found
        HTTPException: 400 if user is inactive
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        token = credentials.credentials
        payload = verify_token(token)
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")

        if user_id is None or token_type != "access":
            raise credentials_exception

    except jwt.InvalidTokenError:
        raise credentials_exception

    # Get user from database
    user = db.query(User).filter(User.id == int(user_id)).first()

    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )

    return user


def get_optional_user(
    db: Session = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))
) -> Optional[User]:
    """
    Get current user if authenticated, None otherwise.

    Args:
        db: Database session
        credentials: Optional HTTP Bearer credentials

    Returns:
        User if authenticated, None otherwise
    """
    if credentials is None:
        return None

    try:
        return get_current_user(credentials, db)
    except HTTPException:
        return None


ADMIN_SESSION_COOKIE = "admin_session"


def require_admin(request: Request) -> None:
    """
    Require admin authentication via session cookie.

    Args:
        request: FastAPI request object

    Raises:
        HTTPException: 401 if not authenticated as admin

    Note:
        For MVP, we check for presence of admin_session cookie.
        Production TODO: Validate session in database or cache
    """
    admin_session = request.cookies.get(ADMIN_SESSION_COOKIE)

    if not admin_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
            headers={"WWW-Authenticate": "Cookie"},
        )

    # MVP: Just check cookie exists
    # Production TODO: Validate session token, check expiry, etc.
```

**Step 4: Add test fixtures**

Add to `app/tests/conftest.py` (or create if doesn't exist):

```python
"""Pytest configuration and fixtures."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base

# Use in-memory SQLite for tests
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db() -> Session:
    """Create test database session."""
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
```

**Step 5: Run tests to verify they pass**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/core/test_deps.py -v
```

Expected: All tests PASS

**Step 6: Commit**

```bash
git add app/core/deps.py app/tests/core/test_deps.py app/tests/conftest.py
git commit -m "feat: add authentication dependencies for FastAPI"
```

---

## Phase 2: Authentication Endpoints

### Task 7: Create Auth Router with Apple Sign In

**Files:**
- Create: `app/routers/auth.py`
- Create: `app/tests/routers/test_auth.py`

**Step 1: Write test for Apple Sign In endpoint**

Create `app/tests/routers/test_auth.py`:

```python
"""Tests for authentication endpoints."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.user import User

client = TestClient(app)


def test_apple_signin_new_user(db: Session, monkeypatch):
    """Test Apple Sign In creates new user."""
    # Mock Apple token verification
    def mock_verify_apple_token(id_token):
        return {
            "sub": "001234.abcd1234",
            "email": "newuser@icloud.com",
            "email_verified": True
        }

    monkeypatch.setattr("app.routers.auth.verify_apple_token", mock_verify_apple_token)

    response = client.post(
        "/auth/apple",
        json={
            "id_token": "mock.apple.token",
            "email": "newuser@icloud.com",
            "full_name": "New User"
        }
    )

    assert response.status_code == 200
    data = response.json()

    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "newuser@icloud.com"
    assert data["user"]["full_name"] == "New User"


def test_apple_signin_existing_user(db: Session, monkeypatch):
    """Test Apple Sign In with existing user."""
    # Create existing user
    existing_user = User(
        apple_id="001234.existing",
        email="existing@icloud.com",
        full_name="Existing User"
    )
    db.add(existing_user)
    db.commit()

    # Mock Apple token verification
    def mock_verify_apple_token(id_token):
        return {
            "sub": "001234.existing",
            "email": "existing@icloud.com"
        }

    monkeypatch.setattr("app.routers.auth.verify_apple_token", mock_verify_apple_token)

    response = client.post(
        "/auth/apple",
        json={
            "id_token": "mock.apple.token",
            "email": "existing@icloud.com"
        }
    )

    assert response.status_code == 200
    data = response.json()

    assert data["user"]["id"] == existing_user.id
    assert data["user"]["email"] == "existing@icloud.com"


def test_apple_signin_invalid_token(monkeypatch):
    """Test Apple Sign In with invalid token."""
    # Mock Apple token verification to raise error
    def mock_verify_apple_token(id_token):
        raise ValueError("Invalid token")

    monkeypatch.setattr("app.routers.auth.verify_apple_token", mock_verify_apple_token)

    response = client.post(
        "/auth/apple",
        json={
            "id_token": "invalid.token",
            "email": "test@icloud.com"
        }
    )

    assert response.status_code == 401
    assert "Invalid Apple token" in response.json()["detail"]
```

**Step 2: Run test to verify it fails**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/routers/test_auth.py::test_apple_signin_new_user -v
```

Expected: FAIL - 404 Not Found (route doesn't exist)

**Step 3: Implement Apple Sign In endpoint**

Create `app/routers/auth.py`:

```python
"""Authentication endpoints."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    verify_apple_token,
)
from app.models.user import AppleSignInRequest, TokenResponse, User, UserResponse

router = APIRouter()


@router.post("/apple", response_model=TokenResponse)
def apple_signin(
    request: AppleSignInRequest,
    db: Session = Depends(get_db)
) -> TokenResponse:
    """
    Authenticate with Apple Sign In.

    Creates new user if first time, otherwise logs in existing user.

    Args:
        request: Apple Sign In request with id_token, email, and optional full_name
        db: Database session

    Returns:
        Access token, refresh token, and user data

    Raises:
        HTTPException: 401 if Apple token is invalid
    """
    # Verify Apple identity token
    try:
        apple_claims = verify_apple_token(request.id_token)
        apple_id = apple_claims.get("sub")

        if not apple_id:
            raise ValueError("Missing subject in token")

    except (ValueError, Exception) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Apple token: {str(e)}"
        )

    # Check if user already exists
    user = db.query(User).filter(User.apple_id == apple_id).first()

    if user is None:
        # Create new user
        user = User(
            apple_id=apple_id,
            email=request.email,
            full_name=request.full_name,
            is_active=True
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # Generate tokens
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.from_orm(user)
    )
```

**Step 4: Mount router in main.py**

Add to `app/main.py` (with other router imports and includes):

```python
from app.routers import auth

app.include_router(auth.router, prefix="/auth", tags=["auth"])
```

**Step 5: Run tests to verify they pass**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/routers/test_auth.py -v
```

Expected: All tests PASS

**Step 6: Commit**

```bash
git add app/routers/auth.py app/tests/routers/test_auth.py app/main.py
git commit -m "feat: add Apple Sign In authentication endpoint"
```

---

### Task 8: Add Token Refresh Endpoint

**Files:**
- Modify: `app/routers/auth.py`
- Modify: `app/tests/routers/test_auth.py`

**Step 1: Add test for token refresh**

Add to `app/tests/routers/test_auth.py`:

```python
from app.core.security import create_refresh_token


def test_refresh_token_valid(db: Session):
    """Test token refresh with valid refresh token."""
    # Create user
    user = User(
        apple_id="001234.refresh",
        email="refresh@icloud.com",
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Create refresh token
    refresh_token = create_refresh_token(user.id)

    response = client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_token}
    )

    assert response.status_code == 200
    data = response.json()

    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_refresh_token_invalid():
    """Test token refresh with invalid token."""
    response = client.post(
        "/auth/refresh",
        json={"refresh_token": "invalid.token"}
    )

    assert response.status_code == 401


def test_refresh_token_with_access_token(db: Session):
    """Test refresh endpoint rejects access tokens."""
    user = User(
        apple_id="001234.wrongtype",
        email="wrongtype@icloud.com",
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Try with access token (should fail)
    access_token = create_access_token(user.id)

    response = client.post(
        "/auth/refresh",
        json={"refresh_token": access_token}
    )

    assert response.status_code == 401
```

**Step 2: Run test to verify it fails**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/routers/test_auth.py::test_refresh_token_valid -v
```

Expected: FAIL - 404 Not Found

**Step 3: Add Pydantic schema and implement refresh endpoint**

Add to `app/models/user.py`:

```python
class RefreshTokenRequest(BaseModel):
    """Request schema for token refresh."""

    refresh_token: str


class AccessTokenResponse(BaseModel):
    """Response schema for token refresh."""

    access_token: str
    token_type: str = "bearer"
```

Add to `app/routers/auth.py`:

```python
import jwt
from app.models.user import RefreshTokenRequest, AccessTokenResponse
from app.core.security import verify_token


@router.post("/refresh", response_model=AccessTokenResponse)
def refresh_token(
    request: RefreshTokenRequest,
    db: Session = Depends(get_db)
) -> AccessTokenResponse:
    """
    Refresh access token using refresh token.

    Args:
        request: Refresh token request
        db: Database session

    Returns:
        New access token

    Raises:
        HTTPException: 401 if refresh token is invalid
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid refresh token"
    )

    try:
        payload = verify_token(request.refresh_token)
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")

        if user_id is None or token_type != "refresh":
            raise credentials_exception

    except jwt.InvalidTokenError:
        raise credentials_exception

    # Verify user exists and is active
    user = db.query(User).filter(User.id == int(user_id)).first()

    if user is None or not user.is_active:
        raise credentials_exception

    # Generate new access token
    access_token = create_access_token(user.id)

    return AccessTokenResponse(access_token=access_token)
```

**Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/routers/test_auth.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add app/routers/auth.py app/tests/routers/test_auth.py app/models/user.py
git commit -m "feat: add token refresh endpoint"
```

---

### Task 9: Add Admin Login Endpoints

**Files:**
- Modify: `app/routers/auth.py`
- Modify: `app/tests/routers/test_auth.py`

**Step 1: Add tests for admin login**

Add to `app/tests/routers/test_auth.py`:

```python
def test_admin_login_valid(monkeypatch):
    """Test admin login with correct password."""
    # Mock settings to have known admin password
    monkeypatch.setenv("ADMIN_PASSWORD", "test_admin_pass")

    response = client.post(
        "/auth/admin/login",
        json={"password": "test_admin_pass"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Logged in as admin"

    # Check cookie is set
    assert "admin_session" in response.cookies


def test_admin_login_invalid():
    """Test admin login with wrong password."""
    response = client.post(
        "/auth/admin/login",
        json={"password": "wrong_password"}
    )

    assert response.status_code == 401
    assert "admin_session" not in response.cookies


def test_admin_logout():
    """Test admin logout."""
    # First login
    response = client.post(
        "/auth/admin/login",
        json={"password": "test_admin_pass"}
    )

    # Then logout
    cookies = {"admin_session": response.cookies["admin_session"]}
    response = client.post("/auth/admin/logout", cookies=cookies)

    assert response.status_code == 200
    # Cookie should be deleted (set to empty with max_age=0)
    assert response.cookies.get("admin_session") == ""
```

**Step 2: Run test to verify it fails**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/routers/test_auth.py::test_admin_login_valid -v
```

Expected: FAIL - 404 Not Found

**Step 3: Add Pydantic schemas**

Add to `app/models/user.py`:

```python
class AdminLoginRequest(BaseModel):
    """Request schema for admin login."""

    password: str


class AdminLoginResponse(BaseModel):
    """Response schema for admin login."""

    message: str
```

**Step 4: Implement admin login endpoints**

Add to `app/routers/auth.py`:

```python
import secrets
from fastapi import Response
from app.core.security import verify_admin_password
from app.models.user import AdminLoginRequest, AdminLoginResponse
from app.core.deps import ADMIN_SESSION_COOKIE


# Simple in-memory admin sessions (for MVP)
# Production TODO: Use Redis or database for session storage
admin_sessions = set()


@router.post("/admin/login", response_model=AdminLoginResponse)
def admin_login(
    request: AdminLoginRequest,
    response: Response
) -> AdminLoginResponse:
    """
    Admin login with password.

    Args:
        request: Admin login request with password
        response: FastAPI response to set cookie

    Returns:
        Success message

    Raises:
        HTTPException: 401 if password is incorrect
    """
    if not verify_admin_password(request.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin password"
        )

    # Generate session token
    session_token = secrets.token_urlsafe(32)
    admin_sessions.add(session_token)

    # Set httpOnly cookie
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=session_token,
        httponly=True,
        max_age=7 * 24 * 60 * 60,  # 7 days
        samesite="lax"
    )

    return AdminLoginResponse(message="Logged in as admin")


@router.post("/admin/logout")
def admin_logout(response: Response) -> dict:
    """
    Admin logout.

    Args:
        response: FastAPI response to delete cookie

    Returns:
        Success message
    """
    # Delete cookie
    response.delete_cookie(key=ADMIN_SESSION_COOKIE)

    return {"message": "Logged out"}
```

**Step 5: Update require_admin to validate session**

Modify `app/core/deps.py`, update `require_admin` function:

```python
def require_admin(request: Request) -> None:
    """
    Require admin authentication via session cookie.

    Args:
        request: FastAPI request object

    Raises:
        HTTPException: 401 if not authenticated as admin
    """
    from app.routers.auth import admin_sessions

    admin_session = request.cookies.get(ADMIN_SESSION_COOKIE)

    if not admin_session or admin_session not in admin_sessions:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required"
        )
```

**Step 6: Run tests to verify they pass**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/routers/test_auth.py -v
```

Expected: All tests PASS

**Step 7: Commit**

```bash
git add app/routers/auth.py app/tests/routers/test_auth.py app/models/user.py app/core/deps.py
git commit -m "feat: add admin login/logout endpoints"
```

---

## Phase 3: Database Migration for User-Based Data

### Task 10: Migrate Favorites/Read-Status to User IDs

**Files:**
- Create: `alembic/versions/YYYYMMDD_migrate_to_user_based_tracking.py`

**Step 1: Create manual migration file**

```bash
source .venv/bin/activate && alembic revision -m "migrate to user based tracking"
```

**Step 2: Write migration**

Edit the newly created migration file in `alembic/versions/` and replace with:

```python
"""migrate to user based tracking

Revision ID: <generated>
Revises: <previous_revision>
Create Date: <generated>
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '<keep_generated_value>'
down_revision = '<keep_generated_value>'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Migrate from session_id to user_id."""
    # Delete all existing data (clean slate as per design decision)
    op.execute("DELETE FROM content_favorites")
    op.execute("DELETE FROM content_read_status")
    op.execute("DELETE FROM content_unlikes")

    # Drop old indexes
    op.drop_index('ix_content_favorites_session_id', table_name='content_favorites')
    op.drop_index('ix_content_favorites_content_id', table_name='content_favorites')
    op.drop_index('ix_content_read_status_session_id', table_name='content_read_status')
    op.drop_index('ix_content_read_status_content_id', table_name='content_read_status')
    op.drop_index('ix_content_unlikes_session_id', table_name='content_unlikes')
    op.drop_index('ix_content_unlikes_content_id', table_name='content_unlikes')

    # Drop old unique constraints
    op.drop_constraint('uq_content_favorites_session_content', 'content_favorites', type_='unique')
    op.drop_constraint('uq_content_read_status_session_content', 'content_read_status', type_='unique')
    op.drop_constraint('uq_content_unlikes_session_content', 'content_unlikes', type_='unique')

    # Drop session_id columns
    op.drop_column('content_favorites', 'session_id')
    op.drop_column('content_read_status', 'session_id')
    op.drop_column('content_unlikes', 'session_id')

    # Add user_id columns
    op.add_column('content_favorites', sa.Column('user_id', sa.Integer(), nullable=False))
    op.add_column('content_read_status', sa.Column('user_id', sa.Integer(), nullable=False))
    op.add_column('content_unlikes', sa.Column('user_id', sa.Integer(), nullable=False))

    # Add foreign keys
    op.create_foreign_key('fk_favorites_user', 'content_favorites', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_read_status_user', 'content_read_status', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_unlikes_user', 'content_unlikes', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # Create new indexes
    op.create_index('ix_content_favorites_user_id', 'content_favorites', ['user_id'])
    op.create_index('ix_content_favorites_content_id', 'content_favorites', ['content_id'])
    op.create_index('ix_content_read_status_user_id', 'content_read_status', ['user_id'])
    op.create_index('ix_content_read_status_content_id', 'content_read_status', ['content_id'])
    op.create_index('ix_content_unlikes_user_id', 'content_unlikes', ['user_id'])
    op.create_index('ix_content_unlikes_content_id', 'content_unlikes', ['content_id'])

    # Create new unique constraints
    op.create_unique_constraint('uq_content_favorites_user_content', 'content_favorites', ['user_id', 'content_id'])
    op.create_unique_constraint('uq_content_read_status_user_content', 'content_read_status', ['user_id', 'content_id'])
    op.create_unique_constraint('uq_content_unlikes_user_content', 'content_unlikes', ['user_id', 'content_id'])


def downgrade() -> None:
    """Revert to session_id."""
    # Reverse the process
    op.execute("DELETE FROM content_favorites")
    op.execute("DELETE FROM content_read_status")
    op.execute("DELETE FROM content_unlikes")

    # Drop new constraints and indexes
    op.drop_constraint('uq_content_favorites_user_content', 'content_favorites', type_='unique')
    op.drop_constraint('uq_content_read_status_user_content', 'content_read_status', type_='unique')
    op.drop_constraint('uq_content_unlikes_user_content', 'content_unlikes', type_='unique')

    op.drop_index('ix_content_favorites_user_id', table_name='content_favorites')
    op.drop_index('ix_content_read_status_user_id', table_name='content_read_status')
    op.drop_index('ix_content_unlikes_user_id', table_name='content_unlikes')

    # Drop foreign keys
    op.drop_constraint('fk_favorites_user', 'content_favorites', type_='foreignkey')
    op.drop_constraint('fk_read_status_user', 'content_read_status', type_='foreignkey')
    op.drop_constraint('fk_unlikes_user', 'content_unlikes', type_='foreignkey')

    # Drop user_id columns
    op.drop_column('content_favorites', 'user_id')
    op.drop_column('content_read_status', 'user_id')
    op.drop_column('content_unlikes', 'user_id')

    # Add back session_id columns
    op.add_column('content_favorites', sa.Column('session_id', sa.String(255), nullable=False))
    op.add_column('content_read_status', sa.Column('session_id', sa.String(255), nullable=False))
    op.add_column('content_unlikes', sa.Column('session_id', sa.String(255), nullable=False))

    # Recreate old indexes and constraints
    op.create_index('ix_content_favorites_session_id', 'content_favorites', ['session_id'])
    op.create_index('ix_content_read_status_session_id', 'content_read_status', ['session_id'])
    op.create_index('ix_content_unlikes_session_id', 'content_unlikes', ['session_id'])

    op.create_unique_constraint('uq_content_favorites_session_content', 'content_favorites', ['session_id', 'content_id'])
    op.create_unique_constraint('uq_content_read_status_session_content', 'content_read_status', ['session_id', 'content_id'])
    op.create_unique_constraint('uq_content_unlikes_session_content', 'content_unlikes', ['session_id', 'content_id'])
```

**Step 3: Run migration**

```bash
source .venv/bin/activate && alembic upgrade head
```

Expected: Migration runs successfully, all data deleted, columns updated

**Step 4: Verify schema changes**

```bash
source .venv/bin/activate && python -c "from sqlalchemy import inspect; from app.core.db import engine; insp = inspect(engine); cols = [c['name'] for c in insp.get_columns('content_favorites')]; print('user_id' in cols and 'session_id' not in cols)"
```

Expected: True

**Step 5: Commit**

```bash
git add alembic/versions/*migrate_to_user_based_tracking.py
git commit -m "db: migrate favorites/read-status to user-based tracking"
```

---

### Task 11: Update SQLAlchemy Models

**Files:**
- Modify: `app/models/schema.py`

**Step 1: Update ContentFavorites model**

In `app/models/schema.py`, find `ContentFavorites` class and update:

```python
class ContentFavorites(Base):
    """Content favorites model."""

    __tablename__ = "content_favorites"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content_id = Column(Integer, ForeignKey("contents.id", ondelete="CASCADE"), nullable=False, index=True)
    favorited_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "content_id", name="uq_content_favorites_user_content"),
    )
```

**Step 2: Update ContentReadStatus model**

In `app/models/schema.py`, find `ContentReadStatus` class and update:

```python
class ContentReadStatus(Base):
    """Content read status tracking model."""

    __tablename__ = "content_read_status"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content_id = Column(Integer, ForeignKey("contents.id", ondelete="CASCADE"), nullable=False, index=True)
    read_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "content_id", name="uq_content_read_status_user_content"),
    )
```

**Step 3: Update ContentUnlikes model (if exists)**

In `app/models/schema.py`, find `ContentUnlikes` class and update:

```python
class ContentUnlikes(Base):
    """Content unlikes tracking model."""

    __tablename__ = "content_unlikes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content_id = Column(Integer, ForeignKey("contents.id", ondelete="CASCADE"), nullable=False, index=True)
    unliked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "content_id", name="uq_content_unlikes_user_content"),
    )
```

**Step 4: Verify models match database**

```bash
source .venv/bin/activate && python -c "from app.models.schema import ContentFavorites; print([c.name for c in ContentFavorites.__table__.columns])"
```

Expected: Should include 'user_id', not 'session_id'

**Step 5: Commit**

```bash
git add app/models/schema.py
git commit -m "refactor: update models to use user_id instead of session_id"
```

---

## Phase 4: Update Services for User-Based Tracking

### Task 12: Update Favorites Service

**Files:**
- Modify: `app/services/favorites.py`
- Create/Modify: `app/tests/services/test_favorites.py`

**Step 1: Write test for user-based favorites**

Create `app/tests/services/test_favorites.py`:

```python
"""Tests for favorites service."""
import pytest
from sqlalchemy.orm import Session

from app.models.schema import Content, ContentFavorites
from app.models.user import User
from app.services.favorites import toggle_favorite, get_favorites


def test_toggle_favorite_add(db: Session):
    """Test adding a favorite."""
    # Create test user and content
    user = User(apple_id="test.001", email="test@example.com", is_active=True)
    db.add(user)
    content = Content(
        content_type="article",
        url="https://example.com/article",
        title="Test Article",
        status="completed"
    )
    db.add(content)
    db.commit()
    db.refresh(user)
    db.refresh(content)

    # Add favorite
    result = toggle_favorite(db, content.id, user.id)

    assert result is True  # Added

    # Verify in database
    favorite = db.query(ContentFavorites).filter_by(user_id=user.id, content_id=content.id).first()
    assert favorite is not None


def test_toggle_favorite_remove(db: Session):
    """Test removing a favorite."""
    # Create user, content, and existing favorite
    user = User(apple_id="test.002", email="test2@example.com", is_active=True)
    db.add(user)
    content = Content(
        content_type="article",
        url="https://example.com/article2",
        title="Test Article 2",
        status="completed"
    )
    db.add(content)
    db.commit()
    db.refresh(user)
    db.refresh(content)

    favorite = ContentFavorites(user_id=user.id, content_id=content.id)
    db.add(favorite)
    db.commit()

    # Remove favorite
    result = toggle_favorite(db, content.id, user.id)

    assert result is False  # Removed

    # Verify deleted from database
    favorite = db.query(ContentFavorites).filter_by(user_id=user.id, content_id=content.id).first()
    assert favorite is None


def test_get_favorites(db: Session):
    """Test getting user favorites."""
    # Create user and content
    user = User(apple_id="test.003", email="test3@example.com", is_active=True)
    db.add(user)

    content1 = Content(content_type="article", url="https://example.com/1", title="Article 1", status="completed")
    content2 = Content(content_type="podcast", url="https://example.com/2", title="Podcast 1", status="completed")
    db.add_all([content1, content2])
    db.commit()
    db.refresh(user)

    # Add favorites
    db.add(ContentFavorites(user_id=user.id, content_id=content1.id))
    db.add(ContentFavorites(user_id=user.id, content_id=content2.id))
    db.commit()

    # Get favorites
    favorites = get_favorites(db, user.id)

    assert len(favorites) == 2
    assert favorites[0].id in [content1.id, content2.id]
```

**Step 2: Run test to verify it fails**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/services/test_favorites.py::test_toggle_favorite_add -v
```

Expected: FAIL - Function signature doesn't match or still uses session_id

**Step 3: Update favorites service**

Modify `app/services/favorites.py`, update function signatures:

```python
def toggle_favorite(db: Session, content_id: int, user_id: int) -> bool:
    """
    Toggle favorite status for content.

    Args:
        db: Database session
        content_id: Content ID to favorite/unfavorite
        user_id: User ID

    Returns:
        True if favorited, False if unfavorited
    """
    favorite = (
        db.query(ContentFavorites)
        .filter_by(user_id=user_id, content_id=content_id)
        .first()
    )

    if favorite:
        # Remove favorite
        db.delete(favorite)
        db.commit()
        return False
    else:
        # Add favorite
        favorite = ContentFavorites(user_id=user_id, content_id=content_id)
        db.add(favorite)
        db.commit()
        return True


def get_favorites(
    db: Session,
    user_id: int,
    limit: int = 50,
    cursor: Optional[int] = None
) -> List[Content]:
    """
    Get user's favorited content.

    Args:
        db: Database session
        user_id: User ID
        limit: Maximum results to return
        cursor: Content ID to start after (for pagination)

    Returns:
        List of favorited content
    """
    query = (
        db.query(Content)
        .join(ContentFavorites, Content.id == ContentFavorites.content_id)
        .filter(ContentFavorites.user_id == user_id)
        .order_by(ContentFavorites.favorited_at.desc())
    )

    if cursor:
        query = query.filter(Content.id < cursor)

    return query.limit(limit).all()


def is_favorited(db: Session, content_id: int, user_id: int) -> bool:
    """
    Check if content is favorited by user.

    Args:
        db: Database session
        content_id: Content ID
        user_id: User ID

    Returns:
        True if favorited, False otherwise
    """
    favorite = (
        db.query(ContentFavorites)
        .filter_by(user_id=user_id, content_id=content_id)
        .first()
    )

    return favorite is not None
```

**Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/services/test_favorites.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add app/services/favorites.py app/tests/services/test_favorites.py
git commit -m "refactor: update favorites service to use user_id"
```

---

### Task 13: Update Read Status Service

**Files:**
- Modify: `app/services/read_status.py`
- Create/Modify: `app/tests/services/test_read_status.py`

**Step 1: Write test for user-based read status**

Create `app/tests/services/test_read_status.py`:

```python
"""Tests for read status service."""
from sqlalchemy.orm import Session

from app.models.schema import Content, ContentReadStatus
from app.models.user import User
from app.services.read_status import mark_as_read, mark_as_unread, is_read, get_recently_read


def test_mark_as_read(db: Session):
    """Test marking content as read."""
    user = User(apple_id="test.read.001", email="read@example.com", is_active=True)
    db.add(user)
    content = Content(content_type="article", url="https://example.com/read", title="Read Test", status="completed")
    db.add(content)
    db.commit()
    db.refresh(user)
    db.refresh(content)

    mark_as_read(db, content.id, user.id)

    # Verify in database
    read_status = db.query(ContentReadStatus).filter_by(user_id=user.id, content_id=content.id).first()
    assert read_status is not None


def test_mark_as_unread(db: Session):
    """Test marking content as unread."""
    user = User(apple_id="test.unread.001", email="unread@example.com", is_active=True)
    db.add(user)
    content = Content(content_type="article", url="https://example.com/unread", title="Unread Test", status="completed")
    db.add(content)
    db.commit()
    db.refresh(user)
    db.refresh(content)

    # First mark as read
    read_status = ContentReadStatus(user_id=user.id, content_id=content.id)
    db.add(read_status)
    db.commit()

    # Then mark as unread
    mark_as_unread(db, content.id, user.id)

    # Verify deleted
    read_status = db.query(ContentReadStatus).filter_by(user_id=user.id, content_id=content.id).first()
    assert read_status is None


def test_is_read(db: Session):
    """Test checking read status."""
    user = User(apple_id="test.check.001", email="check@example.com", is_active=True)
    db.add(user)
    content = Content(content_type="article", url="https://example.com/check", title="Check Test", status="completed")
    db.add(content)
    db.commit()
    db.refresh(user)
    db.refresh(content)

    # Not read initially
    assert is_read(db, content.id, user.id) is False

    # Mark as read
    db.add(ContentReadStatus(user_id=user.id, content_id=content.id))
    db.commit()

    # Now read
    assert is_read(db, content.id, user.id) is True
```

**Step 2: Run test to verify it fails**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/services/test_read_status.py::test_mark_as_read -v
```

Expected: FAIL

**Step 3: Update read_status service**

Modify `app/services/read_status.py`:

```python
def mark_as_read(db: Session, content_id: int, user_id: int) -> None:
    """
    Mark content as read for user.

    Args:
        db: Database session
        content_id: Content ID
        user_id: User ID
    """
    # Check if already marked as read
    existing = (
        db.query(ContentReadStatus)
        .filter_by(user_id=user_id, content_id=content_id)
        .first()
    )

    if not existing:
        read_status = ContentReadStatus(user_id=user_id, content_id=content_id)
        db.add(read_status)
        db.commit()


def mark_as_unread(db: Session, content_id: int, user_id: int) -> None:
    """
    Mark content as unread for user (remove read status).

    Args:
        db: Database session
        content_id: Content ID
        user_id: User ID
    """
    read_status = (
        db.query(ContentReadStatus)
        .filter_by(user_id=user_id, content_id=content_id)
        .first()
    )

    if read_status:
        db.delete(read_status)
        db.commit()


def is_read(db: Session, content_id: int, user_id: int) -> bool:
    """
    Check if content is read by user.

    Args:
        db: Database session
        content_id: Content ID
        user_id: User ID

    Returns:
        True if read, False otherwise
    """
    read_status = (
        db.query(ContentReadStatus)
        .filter_by(user_id=user_id, content_id=content_id)
        .first()
    )

    return read_status is not None


def get_recently_read(
    db: Session,
    user_id: int,
    limit: int = 50,
    cursor: Optional[int] = None
) -> List[Content]:
    """
    Get recently read content for user.

    Args:
        db: Database session
        user_id: User ID
        limit: Maximum results
        cursor: Content ID to start after

    Returns:
        List of recently read content
    """
    query = (
        db.query(Content)
        .join(ContentReadStatus, Content.id == ContentReadStatus.content_id)
        .filter(ContentReadStatus.user_id == user_id)
        .order_by(ContentReadStatus.read_at.desc())
    )

    if cursor:
        query = query.filter(Content.id < cursor)

    return query.limit(limit).all()
```

**Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/services/test_read_status.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add app/services/read_status.py app/tests/services/test_read_status.py
git commit -m "refactor: update read_status service to use user_id"
```

---

## Phase 5: Protect API Endpoints

### Task 14: Add Auth to API Content Endpoints

**Files:**
- Modify: `app/routers/api_content.py`
- Modify: `app/tests/routers/test_api_content.py` (if exists)

**Step 1: Update favorite endpoint to use current user**

In `app/routers/api_content.py`, find the favorite endpoint and update:

```python
from app.core.deps import get_current_user
from app.models.user import User


@router.post("/{content_id}/favorite")
def toggle_favorite_endpoint(
    content_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Toggle favorite status for content."""
    from app.services.favorites import toggle_favorite

    is_favorited = toggle_favorite(db, content_id, current_user.id)

    return {
        "content_id": content_id,
        "favorited": is_favorited
    }
```

**Step 2: Update unfavorite endpoint**

```python
@router.delete("/{content_id}/unfavorite")
def unfavorite_endpoint(
    content_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Remove content from favorites."""
    from app.services.favorites import toggle_favorite

    toggle_favorite(db, content_id, current_user.id)

    return {"content_id": content_id, "favorited": False}
```

**Step 3: Update favorites list endpoint**

```python
@router.get("/favorites/list")
def list_favorites_endpoint(
    limit: int = 50,
    cursor: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's favorited content."""
    from app.services.favorites import get_favorites

    favorites = get_favorites(db, current_user.id, limit, cursor)

    return {
        "items": [content_to_dict(c) for c in favorites],
        "cursor": favorites[-1].id if favorites else None
    }
```

**Step 4: Update mark-read endpoint**

```python
@router.post("/{content_id}/mark-read")
def mark_read_endpoint(
    content_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark content as read."""
    from app.services.read_status import mark_as_read

    mark_as_read(db, content_id, current_user.id)

    return {"content_id": content_id, "read": True}
```

**Step 5: Update mark-unread endpoint**

```python
@router.delete("/{content_id}/mark-unread")
def mark_unread_endpoint(
    content_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark content as unread."""
    from app.services.read_status import mark_as_unread

    mark_as_unread(db, content_id, current_user.id)

    return {"content_id": content_id, "read": False}
```

**Step 6: Update recently-read endpoint**

```python
@router.get("/recently-read/list")
def recently_read_endpoint(
    limit: int = 50,
    cursor: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get recently read content."""
    from app.services.read_status import get_recently_read

    recently_read = get_recently_read(db, current_user.id, limit, cursor)

    return {
        "items": [content_to_dict(c) for c in recently_read],
        "cursor": recently_read[-1].id if recently_read else None
    }
```

**Step 7: Test manually with curl**

First, create a test user and get token:

```bash
curl -X POST http://localhost:8000/auth/apple \
  -H "Content-Type: application/json" \
  -d '{"id_token": "test", "email": "test@example.com", "full_name": "Test User"}'
```

Then test an endpoint:

```bash
TOKEN="<access_token_from_above>"
curl -X POST http://localhost:8000/api/content/1/favorite \
  -H "Authorization: Bearer $TOKEN"
```

Expected: 200 OK with favorite response

**Step 8: Commit**

```bash
git add app/routers/api_content.py
git commit -m "feat: add user authentication to API content endpoints"
```

---

### Task 15: Add Admin Auth to Web Routes

**Files:**
- Modify: `app/routers/content.py`
- Modify: `app/routers/admin.py`
- Modify: `app/routers/logs.py`

**Step 1: Add admin dependency to content routes**

In `app/routers/content.py`, add to all route functions:

```python
from app.core.deps import require_admin


@router.get("/")
async def list_content(
    request: Request,
    # ... other params ...
    _: None = Depends(require_admin)  # Add this
):
    """List all content with filters."""
    # ... existing implementation ...


@router.get("/favorites")
async def favorites_page(
    request: Request,
    # ... other params ...
    _: None = Depends(require_admin)  # Add this
):
    """Show favorites page."""
    # ... existing implementation ...


@router.get("/content/{content_id}")
async def view_content(
    request: Request,
    content_id: int,
    _: None = Depends(require_admin)  # Add this
):
    """View single content item."""
    # ... existing implementation ...
```

**Step 2: Add admin dependency to admin routes**

In `app/routers/admin.py`, add to all routes:

```python
from app.core.deps import require_admin


@router.get("/admin/")
async def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin)  # Add this
):
    """Admin dashboard."""
    # ... existing implementation ...
```

**Step 3: Add admin dependency to logs routes**

In `app/routers/logs.py`, add to all routes:

```python
from app.core.deps import require_admin


@router.get("/admin/logs")
async def list_logs(
    request: Request,
    _: None = Depends(require_admin)  # Add this
):
    """List log files."""
    # ... existing implementation ...


@router.get("/admin/logs/{filename:path}")
async def view_log(
    request: Request,
    filename: str,
    _: None = Depends(require_admin)  # Add this
):
    """View log file."""
    # ... existing implementation ...
```

**Step 4: Test admin login flow**

```bash
# Try to access admin without login (should fail)
curl -v http://localhost:8000/admin/

# Login as admin
curl -X POST http://localhost:8000/auth/admin/login \
  -H "Content-Type: application/json" \
  -d '{"password": "admin123"}' \
  -c cookies.txt

# Access admin with cookie (should work)
curl http://localhost:8000/admin/ -b cookies.txt
```

Expected: First request gets 401, after login with cookie gets 200

**Step 5: Commit**

```bash
git add app/routers/content.py app/routers/admin.py app/routers/logs.py
git commit -m "feat: add admin authentication to web routes"
```

---

## Phase 6: iOS Implementation

### Task 16: Create Keychain Manager

**Files:**
- Create: `client/newsly/newsly/Services/KeychainManager.swift`
- Create: `client/newsly/newsly/Services/KeychainManagerTests.swift` (unit tests)

**Step 1: Create KeychainManager implementation**

Create `client/newsly/newsly/Services/KeychainManager.swift`:

```swift
import Foundation
import Security

/// Manages secure storage of authentication tokens in the iOS Keychain
final class KeychainManager {
    static let shared = KeychainManager()

    private init() {}

    private let serviceName = "com.newsly.app"

    enum KeychainKey: String {
        case accessToken = "accessToken"
        case refreshToken = "refreshToken"
        case userId = "userId"
    }

    /// Save a token to the keychain
    func saveToken(_ token: String, key: KeychainKey) {
        guard let data = token.data(using: .utf8) else { return }

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: serviceName,
            kSecAttrAccount as String: key.rawValue,
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlocked
        ]

        // Delete existing item if any
        SecItemDelete(query as CFDictionary)

        // Add new item
        let status = SecItemAdd(query as CFDictionary, nil)

        if status != errSecSuccess {
            print("Keychain save error: \(status)")
        }
    }

    /// Retrieve a token from the keychain
    func getToken(key: KeychainKey) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: serviceName,
            kSecAttrAccount as String: key.rawValue,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]

        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)

        guard status == errSecSuccess,
              let data = result as? Data,
              let token = String(data: data, encoding: .utf8) else {
            return nil
        }

        return token
    }

    /// Delete a specific token from the keychain
    func deleteToken(key: KeychainKey) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: serviceName,
            kSecAttrAccount as String: key.rawValue
        ]

        SecItemDelete(query as CFDictionary)
    }

    /// Clear all authentication data from the keychain
    func clearAll() {
        deleteToken(key: .accessToken)
        deleteToken(key: .refreshToken)
        deleteToken(key: .userId)
    }
}
```

**Step 2: Test Keychain manager manually**

In Xcode:
1. Add the file to the project
2. Build the project (Cmd+B)
3. Create a simple test in a view:

```swift
// Temporary test code
KeychainManager.shared.saveToken("test123", key: .accessToken)
let token = KeychainManager.shared.getToken(key: .accessToken)
print("Retrieved token: \(token ?? "nil")")
```

Expected: Prints "Retrieved token: test123"

**Step 3: Commit**

```bash
git add client/newsly/newsly/Services/KeychainManager.swift
git commit -m "feat(ios): add Keychain manager for secure token storage"
```

---

### Task 17: Create User Model

**Files:**
- Create: `client/newsly/newsly/Models/User.swift`

**Step 1: Create User model**

Create `client/newsly/newsly/Models/User.swift`:

```swift
import Foundation

/// User account model matching backend UserResponse schema
struct User: Codable, Identifiable {
    let id: Int
    let appleId: String
    let email: String
    let fullName: String?
    let isAdmin: Bool
    let isActive: Bool
    let createdAt: Date
    let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id
        case appleId = "apple_id"
        case email
        case fullName = "full_name"
        case isAdmin = "is_admin"
        case isActive = "is_active"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

/// Token response from authentication endpoints
struct TokenResponse: Codable {
    let accessToken: String
    let refreshToken: String
    let tokenType: String
    let user: User

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case tokenType = "token_type"
        case user
    }
}

/// Request for token refresh
struct RefreshTokenRequest: Codable {
    let refreshToken: String

    enum CodingKeys: String, CodingKey {
        case refreshToken = "refresh_token"
    }
}

/// Response for token refresh
struct AccessTokenResponse: Codable {
    let accessToken: String
    let tokenType: String

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case tokenType = "token_type"
    }
}
```

**Step 2: Add to Xcode project**

1. Open Xcode
2. Add User.swift to the Models group
3. Build (Cmd+B) to verify no errors

**Step 3: Commit**

```bash
git add client/newsly/newsly/Models/User.swift
git commit -m "feat(ios): add User model and auth response types"
```

---

### Task 18: Create Authentication Service

**Files:**
- Create: `client/newsly/newsly/Services/AuthenticationService.swift`

**Step 1: Create AuthenticationService**

Create `client/newsly/newsly/Services/AuthenticationService.swift`:

```swift
import Foundation
import AuthenticationServices

/// Authentication service handling Apple Sign In and token management
final class AuthenticationService: NSObject {
    static let shared = AuthenticationService()

    private override init() {
        super.init()
    }

    private var currentNonce: String?

    /// Sign in with Apple
    func signInWithApple() async throws -> User {
        let nonce = randomNonceString()
        currentNonce = nonce

        let appleIDProvider = ASAuthorizationAppleIDProvider()
        let request = appleIDProvider.createRequest()
        request.requestedScopes = [.fullName, .email]
        request.nonce = sha256(nonce)

        let authController = ASAuthorizationController(authorizationRequests: [request])

        return try await withCheckedThrowingContinuation { continuation in
            let delegate = AppleSignInDelegate(continuation: continuation, nonce: nonce)
            authController.delegate = delegate
            authController.presentationContextProvider = delegate
            authController.performRequests()

            // Keep delegate alive
            objc_setAssociatedObject(authController, "delegate", delegate, .OBJC_ASSOCIATION_RETAIN)
        }
    }

    /// Refresh access token using refresh token
    func refreshAccessToken() async throws -> String {
        guard let refreshToken = KeychainManager.shared.getToken(key: .refreshToken) else {
            throw AuthError.noRefreshToken
        }

        let url = URL(string: "\(AppSettings.shared.baseURL)/auth/refresh")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body = RefreshTokenRequest(refreshToken: refreshToken)
        request.httpBody = try? JSONEncoder().encode(body)

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {
            throw AuthError.refreshFailed
        }

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        let tokenResponse = try decoder.decode(AccessTokenResponse.self, from: data)

        // Save new access token
        KeychainManager.shared.saveToken(tokenResponse.accessToken, key: .accessToken)

        return tokenResponse.accessToken
    }

    /// Logout user (clear all tokens)
    func logout() {
        KeychainManager.shared.clearAll()
    }

    /// Get current user from backend
    func getCurrentUser() async throws -> User {
        guard let token = KeychainManager.shared.getToken(key: .accessToken) else {
            throw AuthError.notAuthenticated
        }

        // For now, decode user from token or fetch from backend
        // This is a simplified version - in production you'd call /auth/me
        throw AuthError.notImplemented
    }

    // MARK: - Private Helpers

    private func randomNonceString(length: Int = 32) -> String {
        precondition(length > 0)
        let charset: [Character] = Array("0123456789ABCDEFGHIJKLMNOPQRSTUVXYZabcdefghijklmnopqrstuvwxyz-._")
        var result = ""
        var remainingLength = length

        while remainingLength > 0 {
            let randoms: [UInt8] = (0..<16).map { _ in
                var random: UInt8 = 0
                let errorCode = SecRandomCopyBytes(kSecRandomDefault, 1, &random)
                if errorCode != errSecSuccess {
                    fatalError("Unable to generate nonce. SecRandomCopyBytes failed with OSStatus \(errorCode)")
                }
                return random
            }

            randoms.forEach { random in
                if remainingLength == 0 {
                    return
                }

                if random < charset.count {
                    result.append(charset[Int(random)])
                    remainingLength -= 1
                }
            }
        }

        return result
    }

    private func sha256(_ input: String) -> String {
        let inputData = Data(input.utf8)
        let hashedData = SHA256.hash(data: inputData)
        let hashString = hashedData.compactMap {
            String(format: "%02x", $0)
        }.joined()

        return hashString
    }
}

// MARK: - Errors

enum AuthError: Error, LocalizedError {
    case notAuthenticated
    case noRefreshToken
    case refreshFailed
    case appleSignInFailed
    case notImplemented

    var errorDescription: String? {
        switch self {
        case .notAuthenticated:
            return "Not authenticated"
        case .noRefreshToken:
            return "No refresh token available"
        case .refreshFailed:
            return "Failed to refresh token"
        case .appleSignInFailed:
            return "Apple Sign In failed"
        case .notImplemented:
            return "Not implemented"
        }
    }
}

// MARK: - Apple Sign In Delegate

private class AppleSignInDelegate: NSObject, ASAuthorizationControllerDelegate, ASAuthorizationControllerPresentationContextProviding {
    let continuation: CheckedContinuation<User, Error>
    let nonce: String

    init(continuation: CheckedContinuation<User, Error>, nonce: String) {
        self.continuation = continuation
        self.nonce = nonce
    }

    func authorizationController(controller: ASAuthorizationController, didCompleteWithAuthorization authorization: ASAuthorization) {
        guard let appleIDCredential = authorization.credential as? ASAuthorizationAppleIDCredential else {
            continuation.resume(throwing: AuthError.appleSignInFailed)
            return
        }

        guard let identityTokenData = appleIDCredential.identityToken,
              let identityToken = String(data: identityTokenData, encoding: .utf8) else {
            continuation.resume(throwing: AuthError.appleSignInFailed)
            return
        }

        // Send to backend
        Task {
            do {
                let user = try await self.sendToBackend(
                    identityToken: identityToken,
                    email: appleIDCredential.email,
                    fullName: appleIDCredential.fullName
                )
                continuation.resume(returning: user)
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }

    func authorizationController(controller: ASAuthorizationController, didCompleteWithError error: Error) {
        continuation.resume(throwing: error)
    }

    func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        guard let windowScene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
              let window = windowScene.windows.first else {
            fatalError("No window available")
        }
        return window
    }

    private func sendToBackend(identityToken: String, email: String?, fullName: PersonNameComponents?) async throws -> User {
        let url = URL(string: "\(AppSettings.shared.baseURL)/auth/apple")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let fullNameString = fullName.flatMap { components in
            [components.givenName, components.familyName]
                .compactMap { $0 }
                .joined(separator: " ")
        }

        let body: [String: Any?] = [
            "id_token": identityToken,
            "email": email ?? "",
            "full_name": fullNameString
        ]

        request.httpBody = try? JSONSerialization.data(withJSONObject: body.compactMapValues { $0 })

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {
            throw AuthError.appleSignInFailed
        }

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        let tokenResponse = try decoder.decode(TokenResponse.self, from: data)

        // Save tokens
        KeychainManager.shared.saveToken(tokenResponse.accessToken, key: .accessToken)
        KeychainManager.shared.saveToken(tokenResponse.refreshToken, key: .refreshToken)
        KeychainManager.shared.saveToken(String(tokenResponse.user.id), key: .userId)

        return tokenResponse.user
    }
}
```

**Step 2: Add CryptoKit import for SHA256**

Add to top of file:

```swift
import CryptoKit
```

**Step 3: Add Sign in with Apple capability in Xcode**

1. Select newsly project in Xcode
2. Select newsly target
3. Go to "Signing & Capabilities"
4. Click "+ Capability"
5. Add "Sign in with Apple"

**Step 4: Build to verify no errors**

```bash
cd client/newsly
xcodebuild -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 15' build
```

Expected: Build succeeds

**Step 5: Commit**

```bash
git add client/newsly/newsly/Services/AuthenticationService.swift client/newsly/newsly.xcodeproj/
git commit -m "feat(ios): add Apple Sign In authentication service"
```

---

### Task 19: Create Authentication ViewModel

**Files:**
- Create: `client/newsly/newsly/ViewModels/AuthenticationViewModel.swift`

**Step 1: Create AuthenticationViewModel**

Create `client/newsly/newsly/ViewModels/AuthenticationViewModel.swift`:

```swift
import Foundation
import SwiftUI

/// Authentication state
enum AuthState: Equatable {
    case loading
    case unauthenticated
    case authenticated(User)
}

/// View model managing authentication state
@MainActor
final class AuthenticationViewModel: ObservableObject {
    @Published var authState: AuthState = .loading
    @Published var errorMessage: String?

    private let authService = AuthenticationService.shared

    init() {
        checkAuthStatus()
    }

    /// Check if user is already authenticated on app launch
    func checkAuthStatus() {
        authState = .loading

        // Check if we have a stored access token
        guard KeychainManager.shared.getToken(key: .accessToken) != nil else {
            authState = .unauthenticated
            return
        }

        // TODO: Validate token with backend or decode locally
        // For MVP, we'll just check if token exists
        // In production, call /auth/me to get current user

        // For now, if token exists, consider authenticated
        // This is temporary - we need to implement proper token validation
        authState = .unauthenticated
    }

    /// Sign in with Apple
    func signInWithApple() {
        authState = .loading
        errorMessage = nil

        Task {
            do {
                let user = try await authService.signInWithApple()
                authState = .authenticated(user)
            } catch {
                errorMessage = error.localizedDescription
                authState = .unauthenticated
            }
        }
    }

    /// Logout current user
    func logout() {
        authService.logout()
        authState = .unauthenticated
    }
}
```

**Step 2: Add to Xcode project and build**

```bash
cd client/newsly
xcodebuild -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 15' build
```

Expected: Build succeeds

**Step 3: Commit**

```bash
git add client/newsly/newsly/ViewModels/AuthenticationViewModel.swift
git commit -m "feat(ios): add authentication view model for state management"
```

---

### Task 20: Create Authentication View

**Files:**
- Create: `client/newsly/newsly/Views/AuthenticationView.swift`

**Step 1: Create AuthenticationView**

Create `client/newsly/newsly/Views/AuthenticationView.swift`:

```swift
import SwiftUI
import AuthenticationServices

/// Login screen with Apple Sign In
struct AuthenticationView: View {
    @EnvironmentObject var authViewModel: AuthenticationViewModel

    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            // App logo or title
            VStack(spacing: 8) {
                Image(systemName: "newspaper.fill")
                    .font(.system(size: 60))
                    .foregroundColor(.blue)

                Text("Newsly")
                    .font(.largeTitle)
                    .fontWeight(.bold)
            }

            Spacer()

            // Sign in with Apple button
            SignInWithAppleButton(
                .signIn,
                onRequest: { request in
                    // Configuration handled by AuthenticationService
                },
                onCompletion: { result in
                    // Handled by AuthenticationService
                }
            )
            .signInWithAppleButtonStyle(.black)
            .frame(height: 50)
            .padding(.horizontal, 40)
            .onTapGesture {
                authViewModel.signInWithApple()
            }

            // Error message
            if let errorMessage = authViewModel.errorMessage {
                Text(errorMessage)
                    .foregroundColor(.red)
                    .font(.caption)
                    .padding(.horizontal, 40)
            }

            Spacer()
        }
        .padding()
    }
}

#Preview {
    AuthenticationView()
        .environmentObject(AuthenticationViewModel())
}
```

**Step 2: Add to Xcode project and build**

**Step 3: Commit**

```bash
git add client/newsly/newsly/Views/AuthenticationView.swift
git commit -m "feat(ios): add authentication UI with Apple Sign In button"
```

---

### Task 21: Update App Entry Point

**Files:**
- Modify: `client/newsly/newsly/newslyApp.swift`

**Step 1: Update newslyApp.swift**

Modify `client/newsly/newsly/newslyApp.swift`:

```swift
import SwiftUI

@main
struct newslyApp: App {
    @StateObject private var authViewModel = AuthenticationViewModel()

    var body: some Scene {
        WindowGroup {
            Group {
                switch authViewModel.authState {
                case .authenticated(let user):
                    ContentView()
                        .environmentObject(authViewModel)
                case .unauthenticated:
                    AuthenticationView()
                        .environmentObject(authViewModel)
                case .loading:
                    LoadingView()
                }
            }
        }
    }
}
```

**Step 2: Build and run in simulator**

```bash
cd client/newsly
xcrun simctl boot "iPhone 15"  # Boot simulator
xcodebuild -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 15' build
```

**Step 3: Manually test in Xcode**

1. Open project in Xcode
2. Run on simulator (Cmd+R)
3. Should see AuthenticationView with Apple Sign In button

**Step 4: Commit**

```bash
git add client/newsly/newsly/newslyApp.swift
git commit -m "feat(ios): add authentication gate to app entry point"
```

---

### Task 22: Update APIClient with Bearer Token

**Files:**
- Modify: `client/newsly/newsly/Services/APIClient.swift`

**Step 1: Add Bearer token to requests**

In `APIClient.swift`, update the `request` method:

```swift
func request<T: Decodable>(
    endpoint: String,
    method: String = "GET",
    body: [String: Any]? = nil
) async throws -> T {
    let url = URL(string: "\(AppSettings.shared.baseURL)\(endpoint)")!
    var request = URLRequest(url: url)
    request.httpMethod = method

    // Add Bearer token if available
    if let accessToken = KeychainManager.shared.getToken(key: .accessToken) {
        request.addValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
    }

    if let body = body {
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    }

    let (data, response) = try await URLSession.shared.data(for: request)

    guard let httpResponse = response as? HTTPURLResponse else {
        throw APIError.invalidResponse
    }

    // Handle 401 - token expired, try refresh
    if httpResponse.statusCode == 401 {
        do {
            _ = try await AuthenticationService.shared.refreshAccessToken()
            // Retry request with new token
            return try await self.request(endpoint: endpoint, method: method, body: body)
        } catch {
            // Refresh failed - logout user
            NotificationCenter.default.post(name: .authenticationRequired, object: nil)
            throw APIError.unauthorized
        }
    }

    guard httpResponse.statusCode == 200 else {
        throw APIError.httpError(httpResponse.statusCode)
    }

    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601

    return try decoder.decode(T.self, from: data)
}
```

**Step 2: Add notification for auth required**

Add extension at bottom of file:

```swift
extension Notification.Name {
    static let authenticationRequired = Notification.Name("authenticationRequired")
}

enum APIError: Error {
    case invalidResponse
    case unauthorized
    case httpError(Int)
}
```

**Step 3: Listen for auth notification in AuthenticationViewModel**

Add to `AuthenticationViewModel.init()`:

```swift
init() {
    checkAuthStatus()

    // Listen for authentication required notifications
    NotificationCenter.default.addObserver(
        forName: .authenticationRequired,
        object: nil,
        queue: .main
    ) { [weak self] _ in
        self?.logout()
    }
}
```

**Step 4: Build and verify**

```bash
cd client/newsly
xcodebuild -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 15' build
```

Expected: Build succeeds

**Step 5: Commit**

```bash
git add client/newsly/newsly/Services/APIClient.swift client/newsly/newsly/ViewModels/AuthenticationViewModel.swift
git commit -m "feat(ios): add Bearer token auth and automatic refresh to API client"
```

---

### Task 23: Update Settings View with Logout

**Files:**
- Modify: `client/newsly/newsly/Views/SettingsView.swift`

**Step 1: Add user info and logout button**

Modify `SettingsView.swift`:

```swift
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var authViewModel: AuthenticationViewModel
    @AppStorage("serverHost") private var serverHost = "192.3.250.10"
    @AppStorage("serverPort") private var serverPort = "8000"

    var body: some View {
        NavigationView {
            Form {
                // User section
                Section(header: Text("Account")) {
                    if case .authenticated(let user) = authViewModel.authState {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(user.email)
                                .font(.headline)
                            if let fullName = user.fullName {
                                Text(fullName)
                                    .font(.subheadline)
                                    .foregroundColor(.secondary)
                            }
                        }

                        Button(role: .destructive) {
                            authViewModel.logout()
                        } label: {
                            Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                        }
                    }
                }

                // Server settings
                Section(header: Text("Server")) {
                    TextField("Host", text: $serverHost)
                        .autocapitalization(.none)
                    TextField("Port", text: $serverPort)
                        .keyboardType(.numberPad)
                }
            }
            .navigationTitle("Settings")
        }
    }
}
```

**Step 2: Build and test in Xcode**

1. Build and run in simulator
2. Navigate to Settings tab
3. Verify user email displays
4. Tap "Sign Out"
5. Should return to AuthenticationView

**Step 3: Commit**

```bash
git add client/newsly/newsly/Views/SettingsView.swift
git commit -m "feat(ios): add user profile and logout to settings"
```

---

## Final Steps

### Task 24: End-to-End Testing

**Manual Testing Checklist:**

**Backend:**
- [ ] Create user via Apple Sign In endpoint
- [ ] Refresh access token
- [ ] Admin login with password
- [ ] Access admin routes with cookie
- [ ] Mark content as favorite (with JWT)
- [ ] Mark content as read (with JWT)
- [ ] Verify user isolation (different users see different favorites)

**iOS:**
- [ ] Launch app shows AuthenticationView
- [ ] Tap Apple Sign In (simulator will use test account)
- [ ] Successfully authenticate and see ContentView
- [ ] Favorite an article
- [ ] Mark article as read
- [ ] Close and reopen app (should stay logged in)
- [ ] Sign out from Settings
- [ ] Should return to AuthenticationView

**Test Commands:**

```bash
# Test backend auth flow
curl -X POST http://localhost:8000/auth/apple \
  -H "Content-Type: application/json" \
  -d '{"id_token": "test", "email": "test@example.com", "full_name": "Test User"}'

# Test admin login
curl -X POST http://localhost:8000/auth/admin/login \
  -H "Content-Type: application/json" \
  -d '{"password": "admin123"}' \
  -c cookies.txt

# Test admin route
curl http://localhost:8000/admin/ -b cookies.txt

# Test API with JWT
TOKEN="<your-access-token>"
curl -X POST http://localhost:8000/api/content/1/favorite \
  -H "Authorization: Bearer $TOKEN"
```

---

### Task 25: Update Documentation

**Files:**
- Modify: `README.md` or `docs/`
- Modify: `.env.example`

**Step 1: Update .env.example with all auth variables**

Verify `.env.example` has:

```bash
DATABASE_URL=postgresql://user:password@localhost/newsly
JWT_SECRET_KEY=your-secret-key-change-in-production
ADMIN_PASSWORD=your-admin-password
```

**Step 2: Add authentication section to README**

Add section to README.md:

```markdown
## Authentication

### iOS App
- Users sign in with Apple Sign In only
- JWT tokens stored securely in iOS Keychain
- Automatic token refresh on expiry

### Web Admin
- Admin routes protected by password authentication
- Set `ADMIN_PASSWORD` in `.env`
- Login at `/auth/admin/login`

### API Endpoints
All `/api/content/*` endpoints require JWT Bearer token:

\`\`\`bash
Authorization: Bearer <access_token>
\`\`\`

### Setup

1. Generate JWT secret:
\`\`\`bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
\`\`\`

2. Add to `.env`:
\`\`\`
JWT_SECRET_KEY=<generated-secret>
ADMIN_PASSWORD=<your-password>
\`\`\`

3. Run migrations:
\`\`\`bash
alembic upgrade head
\`\`\`

4. Configure Apple Sign In:
   - Add Sign in with Apple capability in Xcode
   - Configure App ID in Apple Developer portal
```

**Step 3: Commit**

```bash
git add README.md .env.example
git commit -m "docs: add authentication setup instructions"
```

---

### Task 26: Final Verification and Deployment

**Step 1: Run all backend tests**

```bash
source .venv/bin/activate
PYTHONPATH=/Users/willem/Development/news_app pytest app/tests/ -v
```

Expected: All tests pass

**Step 2: Run linting**

```bash
ruff check app/
ruff format app/
```

**Step 3: Build iOS app**

```bash
cd client/newsly
xcodebuild -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 15' clean build
```

Expected: Build succeeds

**Step 4: Create final commit**

```bash
git add -A
git commit -m "feat: complete authentication system implementation

- Add Apple Sign In for iOS users
- Add admin password protection for web routes
- Implement JWT-based API authentication
- Migrate from session_id to user_id for favorites/read-status
- Add Keychain storage for iOS tokens
- Add automatic token refresh on expiry

Closes #<issue-number>"
```

**Step 5: Push to repository**

```bash
git push origin main
```

---

## Implementation Complete!

You now have a fully functional authentication system with:

✅ **Backend:**
- JWT token generation and validation
- Apple Sign In integration
- Admin password authentication
- User-based favorites and read tracking
- Protected API endpoints

✅ **iOS:**
- Apple Sign In flow
- Secure Keychain token storage
- Automatic token refresh
- Authentication-gated app
- User profile and logout

✅ **Database:**
- Users table with Apple ID
- User-based favorites/read-status
- Proper foreign key relationships

**Next steps:**
- Deploy to production server
- Submit iOS app to App Store (requires Apple Developer account setup)
- Monitor authentication errors in production
- Consider adding password reset flow (future enhancement)
