# CLI Reference

Folder-by-folder reference for the Go command-line client, its generated API bindings, runtime helpers, and checked-in contract artifacts.

## What this section covers
- Use this section to trace the CLI entrypoint into command handlers, config loading, output formatting, and runtime API calls.
- Generated artifacts and cache directories are intentionally excluded.

## Top-level folders
| Folder | Focus |
|---|---|
| `cmd/` | `cli/cmd/newsbuddy/main.go`, the `newsbuddy` binary entrypoint. |
| `internal/` | Command wiring, auth/content/search/onboarding/news subcommands, config parsing, output helpers, runtime API client helpers, and generated API bindings. |
| `newsly_agent/` | Legacy Python namespace/cache directory; no tracked source files are currently checked in. |
| `openapi/` | Checked-in `agent-openapi.json` contract used to generate the Go client surface. |

## Concat command
```bash
find docs/codebase/cli -type f -name '*.md' | sort | xargs cat
```
