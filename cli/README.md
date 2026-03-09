# `newsly-agent`

Remote CLI for the Newsly FastAPI server. It is an HTTP client only; it does not read the app database directly.

## Config

Default config path:

```text
~/.config/newsly-agent/config.json
```

Override with:

```bash
export NEWSLY_AGENT_CONFIG=/path/to/config.json
```

Store the server URL and API key:

```bash
python -m cli.newsly_agent.main config set-server https://news.example.com
python -m cli.newsly_agent.main config set-api-key newsly_ak_...
```

## Output

JSON is the default. Use `--output text` for human-readable output.

Stable JSON envelope shape:

```json
{
  "ok": true,
  "command": "content submit",
  "data": {
    "content_id": 42,
    "task_id": 1201
  }
}
```

Errors use the same envelope with `ok: false`.

## Common Commands

```bash
python -m cli.newsly_agent.main content list --limit 10
python -m cli.newsly_agent.main content get 42
python -m cli.newsly_agent.main content submit https://example.com/article --wait
python -m cli.newsly_agent.main content summarize https://example.com/article --wait
python -m cli.newsly_agent.main search "recent AI chip news"
python -m cli.newsly_agent.main jobs get 1201
python -m cli.newsly_agent.main onboarding run --brief "I want startup, infra, and ML news" --wait
python -m cli.newsly_agent.main onboarding complete 77 --accept-all
python -m cli.newsly_agent.main sources list
python -m cli.newsly_agent.main sources add --type atom --feed-url https://example.com/feed.xml
python -m cli.newsly_agent.main digest generate --start-at 2026-03-07T00:00:00Z --end-at 2026-03-08T00:00:00Z --wait
python -m cli.newsly_agent.main digest list --read-filter unread
```

## Wait / Poll Behavior

- `content submit --wait` and `content summarize --wait` poll `GET /api/jobs/{id}` until the task reaches a terminal status.
- `digest generate --wait` polls `GET /api/jobs/{id}`.
- `onboarding run --wait` polls `GET /api/agent/onboarding/{run_id}` until the run reaches a terminal status.
