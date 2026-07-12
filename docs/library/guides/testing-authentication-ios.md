# Testing a Local User in iOS Simulator

Use the stable developer-user workflow for ordinary local testing. It creates realistic
content, prepares Briefing data, and can sign the same user into a booted Simulator.

## Prerequisites

1. Run migrations: `uv run alembic -c migrations/alembic.ini upgrade head`.
2. Start the local API on `http://localhost:8000` with debug endpoints enabled.
3. Build and run the Debug configuration of the iOS app at least once.

## One-command setup and login

```bash
uv run python scripts/dev_user.py setup --launch
```

The command is idempotent. Each run resets the stable `debug+showcase@newsly.local`
identity and creates:

- 8 completed articles and 6 completed podcasts;
- 24 completed Fast Reads across recent days;
- saved and read examples;
- a deterministic Briefing edition;
- a `newsly://debug-login` handoff to the first booted Simulator.

Use real LLM composition when testing the production Briefing generation path:

```bash
uv run python scripts/dev_user.py setup --briefing-mode llm --launch
```

Use `--server-url` or `--simulator` when the defaults are not correct:

```bash
uv run python scripts/dev_user.py login \
  --server-url http://127.0.0.1:8000 \
  --simulator BOOTED_SIMULATOR_UDID
```

The login link is handled only in Debug builds. It replaces stale credentials before
requesting a debug session for the seeded user.

## Inspect the fixture

```bash
uv run python scripts/dev_user.py status
uv run python scripts/dev_user.py --json status
```

Status reports the user flags, article/podcast/news counts, read and saved counts,
Briefing lenses and segments, pending/degraded counts, and the latest refresh task.

## Test Start Here states

The same stable identity can be reset to any deterministic first-edition state:

```bash
uv run python scripts/dev_user.py setup \
  --profile onboarding \
  --state partial_failure \
  --launch
```

Available states are `initial`, `early`, `mid`, `partial_failure`, `delayed`, `ready`,
`resumed`, and `completed`.

## Debug Menu fallback

The Debug Menu is available from the landing screen and Settings in Debug builds. It
shows the active endpoint and user ID. Enter a local database user ID under **Local
User** and tap **Sign In as Local User** to use the same debug-session flow without a
deep link. **Copy Debug Context** copies the endpoint and authenticated identity for
bug reports.

Manual JWT entry remains available for testing token parsing, expiry, and refresh
behavior specifically:

```bash
uv run python scripts/generate_auth_tokens.py --user-id USER_ID
```

Paste those values into **Set Tokens**. This is a fallback, not the normal fixture
setup path.

## Troubleshooting

- **No booted simulator:** Boot the app from Xcode, then rerun `dev_user.py login`.
- **Debug endpoint unavailable:** Start the API with `DEBUG=true` or
  `ENVIRONMENT=development`.
- **Cannot connect:** Verify `curl http://localhost:8000/health` and check the endpoint
  shown in the Debug Menu.
- **Old user still appears:** Rerun `dev_user.py login`; the debug login clears existing
  tokens before authenticating the requested user.
- **Need exact fixture state:** Run `dev_user.py status` before reproducing the issue and
  copy the iOS Debug Context into the bug report.

Apple Sign In still requires a signed physical device and should be tested separately
for release validation.
