# Testing Authentication in iOS Simulator

## Overview

Since Apple Sign In requires a real device with an Apple ID, we've built a **Debug Menu** to test authentication in the iOS Simulator using generated test tokens.

## Prerequisites

1. **Backend Running**: Ensure your FastAPI backend is running on `localhost:8000`
2. **Database Setup**: Run migrations with `alembic upgrade head`
3. **Environment Variables**: Ensure `JWT_SECRET_KEY` and `ADMIN_PASSWORD` are set in `.env`

## Testing Method 1: Using Debug Menu (Recommended)

### Step 1: Generate Test Token

In your terminal, run the authentication test script:

```bash
cd /path/to/news_app
./scripts/test_auth_flow.sh
```

This script will:
- Validate all authentication endpoints
- Create a test user in the database
- Generate valid JWT tokens
- Output a test access token you can use

**Example Output:**
```
✨ All authentication tests passed!

📋 Test Token for iOS Simulator:
================================
Access Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

You can use this token to test the iOS app...
```

### Step 2: Open iOS Simulator

1. Open Xcode project: `open client/newsly/newsly.xcodeproj`
2. Select iOS Simulator (e.g., iPhone 15 Pro)
3. Build and Run (⌘R)

### Step 3: Access Debug Menu

1. App will show authentication screen (Apple Sign In won't work in simulator)
2. At the bottom of the login screen, tap **"🐛 Debug Menu"** button
   - *Note: This button only appears in DEBUG builds*
3. Alternatively, you can access it from Settings tab after authenticating
4. Server configuration (host/port/protocol) now lives inside the Debug Menu

### Step 4: Set Test Token

In the Debug Menu:

1. Tap **"Manually Set Tokens"**
2. Paste the Access Token from Step 1
3. (Optional) Paste Refresh Token if you want to test token refresh
4. Tap **"Save Tokens"**
5. Close the debug menu

### Step 5: Verify Authentication

The app should now be authenticated! You should see:
- ✅ Green "Authenticated" status in Debug Menu
- ✅ User email displayed in Settings > Account section
- ✅ Ability to browse articles, podcasts, and news
- ✅ Ability to favorite and mark items as read

## Testing Method 2: Manual API Testing

If you want to test the backend independently:

```bash
# 1. Create a test user
python3 -c "
from app.core.db import engine
from app.models.schema import Base
from app.models.user import User
from sqlalchemy.orm import Session

Base.metadata.create_all(bind=engine)
session = Session(engine)

user = User(
    apple_id='test.manual.001',
    email='manual@test.com',
    full_name='Manual Test User',
    is_active=True
)
session.add(user)
session.commit()
print(f'Created user ID: {user.id}')
session.close()
"

# 2. Generate token
python3 -c "
from app.core.security import create_access_token
token = create_access_token(1)  # Use the user ID from step 1
print(f'Token: {token}')
"

# 3. Test API endpoint
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  http://localhost:8000/api/content/
```

## Debug Menu Features

The debug menu provides several testing utilities:

### Current Status
- Shows authentication state (Loading/Unauthenticated/Authenticated)
- Displays whether tokens are stored in Keychain
- Shows authenticated user email

### Test Actions
- **Manually Set Tokens**: Paste test tokens from backend
- **Clear All Tokens**: Remove all tokens from Keychain
- **Force Logout**: Clear tokens and return to login screen
- **View Stored Tokens**: See what's currently in Keychain (truncated for security)

## What to Test

### 1. Authentication Flow
- [ ] App shows auth screen on first launch
- [ ] Debug menu accessible from Settings
- [ ] Tokens can be manually entered
- [ ] App transitions to authenticated state
- [ ] User info appears in Settings

### 2. API Integration
- [ ] Content list loads with auth token
- [ ] Content detail view works
- [ ] Favorite action works
- [ ] Mark as read works
- [ ] Filters work (article/podcast/news)

### 3. Token Management
- [ ] Tokens persist across app restarts
- [ ] Logout clears tokens
- [ ] App shows login screen after logout
- [ ] Invalid token shows authentication error

### 4. Error Handling
- [ ] 401 responses trigger re-authentication
- [ ] Network errors show appropriate messages
- [ ] Invalid tokens are handled gracefully

## Testing on Real Device

To test actual Apple Sign In on a real device:

### Prerequisites
1. **Apple Developer Account** (required)
2. **Physical iOS device** with iOS 15+
3. **Xcode signing** configured with your team

### Setup Steps

1. **Enable Sign in with Apple capability**:
   - Open Xcode project
   - Select newsly target
   - Go to "Signing & Capabilities"
   - Click "+ Capability"
   - Add "Sign in with Apple"

2. **Configure App ID**:
   - Go to developer.apple.com
   - Identifiers → Your App ID
   - Enable "Sign in with Apple"
   - Save

3. **Build and Run on Device**:
   - Connect your iPhone/iPad
   - Select it as the run destination
   - Build and run (⌘R)

4. **Test Real Apple Sign In**:
   - Tap "Sign in with Apple"
   - Use your Apple ID
   - Complete authentication
   - Verify user is created in backend database

## Troubleshooting

### "API returns 401"
- Check that backend is running
- Verify token is valid (not expired)
- Check token is properly set in Keychain via Debug Menu

### "App crashes on launch"
- Check console logs in Xcode
- Verify backend URL in Settings is correct
- Try clearing tokens and restarting

### "Debug Menu not visible"
- Debug Menu only appears in DEBUG builds
- Ensure you're running from Xcode, not a release build
- Check Settings tab at bottom of screen

### "Can't connect to backend"
- Verify backend is running: `curl http://localhost:8000/health`
- Check Debug Menu > Server Configuration
- For simulator, use `localhost` or `127.0.0.1`

## Notes

- **Debug Menu is only available in DEBUG builds** - it won't appear in release builds
- **Apple Sign In requires a real device** - it will not work in the simulator
- **Test tokens expire** - default is 30 minutes for access tokens
- **Keychain data persists** - use "Clear All Tokens" to reset between tests

## Next Steps

After validating in simulator:
1. Test on a real device with Apple Sign In
2. Test token refresh flow (wait for token expiry)
3. Test logout and re-authentication
4. Test with multiple users
5. Test network failure scenarios
