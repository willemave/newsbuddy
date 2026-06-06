# CLI Reference

Source folder: `cli`

## Purpose
Standalone Go CLI (`newsbuddy`) for authenticated machine/operator access to the Newsly FastAPI server. It is an HTTP client only; it does not access the app database directly.

## Runtime behavior
- `cmd/newsbuddy/main.go` starts the Cobra command tree from `internal/cmd`.
- Global flags include `--config`, `--server`, `--api-key`, `--output json|text`, `--json`, and `--timeout`.
- Runtime config precedence is flags over env vars over config file over defaults. Config-path env vars include `NEWSBUDDY_CONFIG`, `NEWSBUDDY_CONFIG_PATH`, `NEWSLY_AGENT_CONFIG`, and `NEWSLY_AGENT_CONFIG_PATH`; runtime overrides include `NEWSBUDDY_SERVER`, `NEWSLY_AGENT_SERVER`, `NEWSBUDDY_API_KEY`, and `NEWSLY_AGENT_API_KEY`.
- Default config lives under `~/.config/newsbuddy/config.json`; default library sync root is `~/.local/share/newsbuddy/library`.
- `internal/api` is checked-in generated Go code from the filtered agent OpenAPI contract, not a cache.
- `internal/runtime` wraps generated operations and hand-written HTTP paths where the CLI needs custom behavior, such as QR auth, library sync, and source subscription.

## Command tree
```text
newsbuddy
  auth login
  config show
  config set server|api-key|library-root <value>
  content list|get|submit|summarize
  content submissions list
  jobs get|wait
  library sync
  news list|get|convert|mark-read
  onboarding start|run|status|complete
  search <query>
  sources list|add
  completion bash|zsh|fish|powershell
  version
```

## Important folders
| Folder | Focus |
|---|---|
| `cmd/` | Binary entrypoint (`cli/cmd/newsbuddy/main.go`). |
| `internal/cmd/` | Cobra app, command handlers, output envelopes, wait flags, and command tests. |
| `internal/config/` | Config file/env/flag resolution. |
| `internal/output/` | JSON/text output envelope formatting. |
| `internal/runtime/` | Runtime API client wrappers, polling/wait behavior, QR link/library/source custom calls. |
| `internal/api/` | Generated Go client/server types from `ogen`. |
| `openapi/` | Checked-in filtered agent OpenAPI contract (`agent-openapi.json`). |
| `newsly_agent/` | Legacy Python namespace/cache directory; no tracked source files are currently checked in. |

## Generation and verification
- Regenerate the CLI contract/client with `./scripts/generate_agent_cli_artifacts.sh`.
- The generation flow exports `cli/openapi/agent-openapi.json`, runs `ogen`, patches datetime/null decoding, and formats generated Go files.
- Local CLI smoke tests live in `scripts/test_agent_cli_local_e2e.py`.
- Go package tests run from `cli` with `go test ./...`.

## Concat command
```bash
find docs/codebase/cli -type f -name '*.md' | sort | xargs cat
```
