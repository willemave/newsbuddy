# `newsly-agent`

Standalone Go CLI for the Newsly FastAPI server. It is an authenticated HTTP client only; it does not access the app database directly.

## Build

```bash
cd cli
go build ./cmd/newsly-agent
```

## Config

Default config path:

```text
~/.config/newsly-agent/config.json
```

Override with:

```bash
export NEWSLY_AGENT_CONFIG=/path/to/config.json
```

Compatibility alias:

```bash
export NEWSLY_AGENT_CONFIG_PATH=/path/to/config.json
```

Persist config values:

```bash
cd cli
go run ./cmd/newsly-agent config set server https://news.example.com
go run ./cmd/newsly-agent config set api-key newsly_ak_...
go run ./cmd/newsly-agent config show
```

## Output

JSON is the default. Use `--output text` for terminal-friendly output.

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
go run ./cmd/newsly-agent content list --limit 10
go run ./cmd/newsly-agent content get 42
go run ./cmd/newsly-agent content submit https://example.com/article --wait
go run ./cmd/newsly-agent content summarize https://example.com/article --wait
go run ./cmd/newsly-agent search "recent AI chip news"
go run ./cmd/newsly-agent jobs get 1201
go run ./cmd/newsly-agent jobs wait 1201
go run ./cmd/newsly-agent onboarding start --brief "I want startup, infra, and ML news" --wait
go run ./cmd/newsly-agent onboarding complete 77 --accept-all
go run ./cmd/newsly-agent sources list
go run ./cmd/newsly-agent sources add https://example.com/feed.xml --feed-type atom
go run ./cmd/newsly-agent digest generate --start-at 2026-03-07T00:00:00Z --end-at 2026-03-08T00:00:00Z --wait
go run ./cmd/newsly-agent digest list --read-filter unread
```

## Regeneration

The CLI-specific OpenAPI contract and generated client are checked in. Regenerate both with:

```bash
./scripts/generate_agent_cli_artifacts.sh
```
