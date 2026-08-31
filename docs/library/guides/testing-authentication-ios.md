# Testing Local Authentication in iOS Simulator

The Rust API exposes `POST /auth/debug/new-user` only in development mode. The
endpoint creates or updates a debug user and returns the normal typed token/user
response. Native UI tests and local launch helpers use this boundary directly;
there is no Python fixture backend.

## Automated local launch

Start the Rust runtime, boot an iOS Simulator, then run:

```bash
./scripts/start_services.sh all --env-file .env --local-e2e --port 8010
./scripts/codex_run_ios.sh --api-base-url http://127.0.0.1:8010
```

The helper builds and installs the current checkout, creates a completed debug
user through the Rust endpoint, and launches the app with the same E2E arguments
used by native UI tests.

## Manual debug user

```bash
response="$(
  curl --fail --silent --show-error \
    --request POST \
    --header 'Content-Type: application/json' \
    --data '{"has_completed_onboarding":true,"has_completed_new_user_tutorial":true}' \
    http://127.0.0.1:8010/auth/debug/new-user
)"
echo "$response" | jq '.user.id, .is_new_user'
```

In a Debug build, the Debug Menu can sign in with that user ID. For scripted
launches, pass `newslyE2EEnabled`, `newslyE2EAutoLogin`, the server host/port,
and `newslyE2EUserId` as launch arguments; see
`client/newsly/newslyUITests/newslyUITests.swift` for the canonical set.

## Native lifecycle coverage

The UI test target creates its own user and skips authenticated lifecycle tests
when a development API is not reachable:

```bash
xcodebuild test \
  -project client/newsly/newsly.xcodeproj \
  -scheme newsly \
  -destination 'platform=iOS Simulator,OS=latest,name=iPhone 17' \
  -parallel-testing-enabled NO
```

Apple Sign In still requires the appropriate signed-device environment and is a
separate release validation path.
