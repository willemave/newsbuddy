# `newsbuddy`

Standalone Go CLI for the Newsbuddy FastAPI server. It is an authenticated HTTP client only; it does not access the app database directly.

## Install

```bash
brew tap willemave/newsbuddy
brew install newsbuddy
```

## Build

```bash
cd cli
go build ./cmd/newsbuddy
```

## Config

Default config path:

```text
~/.config/newsbuddy/config.json
```

Override with:

```bash
export NEWSBUDDY_CONFIG=/path/to/config.json
```

Compatibility aliases:

```bash
export NEWSBUDDY_CONFIG_PATH=/path/to/config.json
export NEWSLY_AGENT_CONFIG=/path/to/config.json
export NEWSLY_AGENT_CONFIG_PATH=/path/to/config.json
```

Persist config values:

```bash
cd cli
go run ./cmd/newsbuddy config set server https://news.example.com
go run ./cmd/newsbuddy config set api-key newsly_ak_...
go run ./cmd/newsbuddy config show
```

Link the CLI to the mobile app with a terminal QR code:

```bash
cd cli
go run ./cmd/newsbuddy --server http://localhost:8000 auth login
```

## Output

JSON is the default. Use `--output text` for terminal-friendly output, or `--json`
as a shortcut back to JSON in scripts.

Stable JSON envelope shape:

```json
{
  "ok": true,
  "command": "content.submit",
  "data": {
    "content_id": 42,
    "task_id": 1201
  }
}
```

Errors use the same envelope with `ok: false`.

## Common Commands

```bash
cd cli
go run ./cmd/newsbuddy content list --limit 10
go run ./cmd/newsbuddy content get 42
go run ./cmd/newsbuddy content submit https://example.com/article --wait
go run ./cmd/newsbuddy content summarize https://example.com/article --wait
go run ./cmd/newsbuddy content submissions list --limit 10
go run ./cmd/newsbuddy search "recent AI chip news"
go run ./cmd/newsbuddy jobs get 1201
go run ./cmd/newsbuddy jobs wait 1201
go run ./cmd/newsbuddy onboarding start --brief "I want startup, infra, and ML news" --wait
go run ./cmd/newsbuddy onboarding complete 77 --accept-all
go run ./cmd/newsbuddy sources list
go run ./cmd/newsbuddy sources add https://example.com/feed.xml --feed-type atom
go run ./cmd/newsbuddy news list --read-filter unread
go run ./cmd/newsbuddy news get 123
go run ./cmd/newsbuddy news mark-read 123
go run ./cmd/newsbuddy news convert 123
```

Notes:

- `content submit --wait` and `content summarize --wait` now block until the submitted item is fetchable via `content get`, not just until the first async job reaches a terminal state.
- `content summarize` submits the URL in "favorite and mark read" mode so the finished item is saved and marked read once processing completes.
- `sources add --feed-type` accepts `atom`, `substack`, or `podcast_rss`.
- `library sync` treats the remote markdown manifest as the desired local state and prunes tracked files that disappear remotely. If the remote manifest is empty while local files are tracked, the CLI refuses to delete everything unless you pass `--allow-prune-all`.

## Regeneration

The CLI-specific OpenAPI contract and registry-generated Go API models are checked in. Regenerate both with:

```bash
./scripts/generate_agent_cli_artifacts.sh
```

## Local Smoke Test

To exercise the local CLI against a local backend, including the real QR auth flow and markdown library sync:

```bash
python3 scripts/test_agent_cli_local_e2e.py --fresh-auth
```

The script:

- checks `http://localhost:8000/health`
- builds the current local CLI into `.tmp/newsbuddy-local-smoke/`
- stores an isolated CLI config and library root under `.tmp/newsbuddy-local-smoke/`
- runs `auth login` and waits for you to approve the QR link in the Newsbuddy app
- exercises `content list`, `content get` when available, `sources list`, and `library sync`

To also exercise the submit-and-wait path:

```bash
python3 scripts/test_agent_cli_local_e2e.py \
  --fresh-auth \
  --submit-url https://example.com/article
```

For a fuller local sweep with an isolated debug user, generated fixtures, source
subscription, news actions, jobs, search, and seeded onboarding status/complete:

```bash
uv run python scripts/test_agent_cli_local_e2e.py \
  --fresh-auth \
  --auto-debug-auth \
  --seed-test-data \
  --exercise-all
```

`--run-onboarding-start` also exercises `onboarding start`, but that path can
invoke live LLM/search providers before returning.

Useful knobs for slower local Docker stacks:

```bash
python3 scripts/test_agent_cli_local_e2e.py \
  --server http://127.0.0.1:8011 \
  --skip-auth \
  --cli-timeout 60s \
  --submit-url https://example.com/article \
  --submit-wait-timeout 4m
```
