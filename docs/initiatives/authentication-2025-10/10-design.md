# Authentication System Design

**Date:** 2025-10-24
**Status:** Approved for implementation

## Overview

Add user authentication to the news_app with Apple Sign In for iOS users and simple admin password protection for web routes. The system uses JWT tokens for the iOS API and session-based auth for web admin access.

## Design Decisions

### User Flows
- **iOS users:** Apple Sign In only (no email/password registration)
- **Web users:** Admin-only access with simple password protection
- **Existing data:** Delete all `session_id="default"` data on migration (clean slate)
- **Anonymous access:** None - authentication required to use the app
- **Email verification:** None - accounts immediately active

### Technical Architecture
- **Backend:** FastAPI + PyJWT + authlib
- **iOS:** Async/await + Keychain + AuthenticationServices
- **API auth:** JWT tokens (Bearer auth) for iOS
- **Web auth:** Session-based with httpOnly cookies for admin
- **Database:** Add `users` table, replace `session_id` with `user_id` in favorites/read-status

### Admin Access
- Single admin password in environment variable (`ADMIN_PASSWORD`)
- User accounts can have `is_admin=true` flag for future role-based access
- Admin routes use separate session-based authentication

## Database Schema

### New Table: users

```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    apple_id VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255),
    is_admin BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_users_apple_id ON users(apple_id);
CREATE INDEX idx_users_email ON users(email);
```

### Modified Tables

**content_favorites:**
- Remove: `session_id VARCHAR(255)`
- Add: `user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE`
- Update unique constraint: `(user_id, content_id)`
- Update indexes: `idx_favorites_user_id`, `idx_favorites_content_id`

**content_read_status:**
- Remove: `session_id VARCHAR(255)`
- Add: `user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE`
- Update unique constraint: `(user_id, content_id)`
- Update indexes: `idx_read_status_user_id`, `idx_read_status_content_id`

**content_unlikes:**
- Remove: `session_id VARCHAR(255)`
- Add: `user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE`
- Update unique constraint: `(user_id, content_id)`
- Update indexes: `idx_unlikes_user_id`, `idx_unlikes_content_id`

### Migration Strategy

1. Delete all existing favorites/read-status/unlikes records (clean slate)
2. Create `users` table
3. Drop `session_id` columns from favorites/read-status/unlikes
4. Add `user_id` columns with foreign keys
5. Recreate indexes and constraints

## Backend Components

### New Files

**`app/core/security.py`** - Security utilities
- `create_access_token(user_id: int) -> str` - JWT with 30min expiry
- `create_refresh_token(user_id: int) -> str` - JWT with 7 day expiry
- `verify_token(token: str) -> dict` - Validates JWT
- `verify_apple_token(id_token: str) -> dict` - Validates Apple identity token
- `verify_admin_password(password: str) -> bool` - Checks env variable

**`app/core/deps.py`** - FastAPI dependencies
- `get_current_user(token: str) -> User` - OAuth2PasswordBearer, extracts user from JWT
- `require_admin(request: Request) -> None` - Checks admin session
- `get_optional_user() -> User | None` - Returns user if authenticated

**`app/routers/auth.py`** - Authentication endpoints
- `POST /auth/apple` - Apple Sign In (body: `{id_token, email, full_name}`)
- `POST /auth/refresh` - Refresh token (body: `{refresh_token}`)
- `POST /auth/admin/login` - Admin login (body: `{password}`)
- `POST /auth/admin/logout` - Admin logout

**`app/models/user.py`** - User SQLAlchemy model and Pydantic schemas
- `User` (SQLAlchemy model)
- `UserCreate`, `UserResponse`, `AppleSignInRequest`, `TokenResponse` (Pydantic)

### Modified Files

**`app/core/settings.py`** - Add auth configuration
```python
JWT_SECRET_KEY: str
JWT_ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
REFRESH_TOKEN_EXPIRE_DAYS: int = 7
ADMIN_PASSWORD: str
```

**`app/main.py`** - Mount auth router
```python
from app.routers import auth
app.include_router(auth.router, prefix="/auth", tags=["auth"])
```

**`app/routers/content.py`** - Add admin dependency to all routes
```python
from app.core.deps import require_admin

@router.get("/")
async def list_content(..., _: None = Depends(require_admin)):
    ...
```

**`app/routers/admin.py`** - Add admin dependency
```python
from app.core.deps import require_admin

@router.get("/admin/")
async def admin_dashboard(..., _: None = Depends(require_admin)):
    ...
```

**`app/routers/logs.py`** - Add admin dependency
```python
from app.core.deps import require_admin
# Apply to all routes
```

**`app/routers/api_content.py`** - Add user dependency to all routes
```python
from app.core.deps import get_current_user

@router.post("/api/content/{content_id}/favorite")
async def toggle_favorite(..., current_user: User = Depends(get_current_user)):
    # Use current_user.id instead of session_id
```

**`app/services/favorites.py`** - Use user_id instead of session_id
```python
def toggle_favorite(db: Session, content_id: int, user_id: int) -> bool:
    # Replace session_id="default" with user_id parameter
```

**`app/services/read_status.py`** - Use user_id instead of session_id
```python
def mark_as_read(db: Session, content_id: int, user_id: int) -> None:
    # Replace session_id="default" with user_id parameter
```

### Dependencies to Add

```toml
[project.dependencies]
PyJWT = "^2.8.0"
authlib = "^1.3.0"
python-multipart = "^0.0.6"  # Already installed
```

## iOS Components

### New Files

**`Models/User.swift`**
```swift
struct User: Codable, Identifiable {
    let id: Int
    let appleId: String
    let email: String
    let fullName: String?
    let isAdmin: Bool
    let createdAt: Date
}
```

**`Services/AuthenticationService.swift`**
- `signInWithApple() async throws -> User`
- `refreshAccessToken() async throws -> String`
- `logout()`
- `getCurrentUser() async throws -> User`

**`Services/KeychainManager.swift`**
- `saveToken(_ token: String, key: String)`
- `getToken(key: String) -> String?`
- `deleteToken(key: String)`
- `clearAll()`

**`ViewModels/AuthenticationViewModel.swift`**
```swift
enum AuthState {
    case unauthenticated
    case authenticated(User)
    case loading
}

@MainActor
class AuthenticationViewModel: ObservableObject {
    @Published var authState: AuthState = .loading
    @Published var errorMessage: String?

    func signInWithApple()
    func checkAuthStatus()
    func logout()
}
```

**`Views/AuthenticationView.swift`**
- Apple Sign In button (native `SignInWithAppleButton`)
- Loading and error states
- Simple, centered UI

### Modified Files

**`newslyApp.swift`**
```swift
@StateObject private var authViewModel = AuthenticationViewModel()

var body: some Scene {
    WindowGroup {
        Group {
            switch authViewModel.authState {
            case .authenticated(let user):
                ContentView().environmentObject(authViewModel)
            case .unauthenticated:
                AuthenticationView().environmentObject(authViewModel)
            case .loading:
                LoadingView()
            }
        }
        .onAppear { authViewModel.checkAuthStatus() }
    }
}
```

**`Services/APIClient.swift`**
- Add `Authorization: Bearer <token>` header to all requests
- Handle 401 responses → attempt token refresh
- If refresh fails → logout user

**`Views/SettingsView.swift`**
- Display current user email
- Add "Sign Out" button

**`Services/APIEndpoints.swift`**
```swift
static let appleSignIn = "/auth/apple"
static let refreshToken = "/auth/refresh"
```

### iOS Configuration

**Xcode Project Settings:**
1. Add "Sign in with Apple" capability
2. Configure App ID with Sign in with Apple enabled
3. Add Apple Developer Team

**Info.plist:**
- Already configured for HTTP exceptions (local dev server)

## Authentication Flows

### iOS: Apple Sign In (First Time)

1. User taps "Sign in with Apple"
2. `AuthenticationView` → `authViewModel.signInWithApple()`
3. `AuthenticationService` → `ASAuthorizationAppleIDProvider().createRequest()`
4. Apple presents native sign-in modal
5. Apple returns `ASAuthorization` with `identityToken` + `email` + `fullName`
6. `AuthenticationService` → `POST /auth/apple` with `{id_token, email, full_name}`
7. Backend validates token with Apple, creates user, returns `{access_token, refresh_token, user}`
8. Store tokens in Keychain: `KeychainManager.saveToken(accessToken, "accessToken")`
9. Update `authState = .authenticated(user)`
10. App switches to `ContentView`

### iOS: Subsequent Logins

1. App launches → `authViewModel.checkAuthStatus()`
2. Check Keychain for `accessToken`
3. If exists → Validate with `GET /auth/me` or decode JWT locally
4. If valid → Set `authState = .authenticated(user)`
5. If expired → Call `POST /auth/refresh` with `refreshToken`
6. If refresh succeeds → Update `accessToken`, set authenticated
7. If refresh fails → Set `authState = .unauthenticated`, show login

### iOS: API Requests with Auth

1. User actions trigger API calls (mark read, favorite, etc.)
2. `APIClient.request()` → Add `Authorization: Bearer <token>` header
3. If 401 response → `refreshAccessToken()`
4. Retry original request with new token
5. If refresh fails → Logout user

### Web: Admin Login

1. Navigate to `/` (any web route)
2. If no valid admin session → Redirect to `/auth/admin/login`
3. Admin enters password → `POST /auth/admin/login`
4. Backend validates password, creates session, sets `admin_session` httpOnly cookie
5. Redirect to original route
6. All subsequent requests include cookie automatically

## Security Considerations

- **JWT secrets:** Store `JWT_SECRET_KEY` in `.env`, never commit
- **Admin password:** Store `ADMIN_PASSWORD` in `.env`, use strong password
- **Token expiry:** Access tokens 30min, refresh tokens 7 days
- **Keychain security:** iOS tokens stored with `kSecAttrAccessibleWhenUnlocked`
- **HTTPS:** Require HTTPS in production (already configured for local dev IPs)
- **Token validation:** Always verify JWT signature and expiration
- **Apple token verification:** Use authlib to verify with Apple's public keys
- **Session cookies:** httpOnly, secure (in production), SameSite=Lax

## Testing Strategy

### Backend Tests
- `tests/routers/test_auth.py` - Auth endpoint tests
- `tests/core/test_security.py` - JWT creation/validation, Apple token mocking
- `tests/routers/test_api_content.py` - Update to include auth headers
- Mock Apple token verification in tests

### iOS Tests
- Unit tests for `AuthenticationService` with mocked backend
- Unit tests for `KeychainManager`
- Unit tests for `AuthenticationViewModel` state transitions
- Integration tests for login flow (XCTest UI)

## Rollout Plan

1. **Phase 1: Backend Infrastructure**
   - Create `users` table migration
   - Implement auth endpoints
   - Add dependencies (get_current_user, require_admin)
   - Update services to use user_id

2. **Phase 2: Protect Endpoints**
   - Add auth to API routes
   - Add admin auth to web routes
   - Test all endpoints with auth

3. **Phase 3: iOS Implementation**
   - Implement Keychain manager
   - Implement authentication service
   - Build auth UI
   - Update API client
   - Test login flow

4. **Phase 4: Testing & Deployment**
   - Write tests
   - Test on iOS device
   - Deploy backend with migrations
   - Release iOS app update

## Open Questions

None - design approved for implementation.

## References

- FastAPI Security: https://fastapi.tiangolo.com/tutorial/security/
- PyJWT: https://pyjwt.readthedocs.io/
- Apple Sign In (iOS): https://developer.apple.com/documentation/sign_in_with_apple
- Authlib: https://docs.authlib.org/
