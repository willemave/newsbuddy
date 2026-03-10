# Go CLI Rewrite Plan

**Date:** 2026-03-09  
**Scope:** `cli/` rewrite, CLI-specific OpenAPI contract cleanup, Go build/test wiring

---

## Summary

The current CLI is a small Python `argparse` wrapper over authenticated FastAPI routes. It works, but it has drift between docs and command behavior, no clean CLI-specific API contract, and no release path for a standalone binary.

This initiative replaces `cli/` with a Go-based standalone binary built around:

- a filtered CLI-specific OpenAPI export derived from FastAPI,
- a generated typed Go client,
- a Cobra command surface optimized for scripting and stable JSON output.

The binary remains `newsly-agent`.

---

## Why This Shape

- The CLI is an HTTP client, not a local data-processing tool, so runtime performance is secondary to portability and maintainability.
- Go is a better fit than Rust here because it gives a straightforward static binary, easy cross-compilation, and less implementation overhead for a small API client.
- FastAPI already emits OpenAPI, so the right move is to derive a clean CLI contract from the server instead of hand-maintaining request structs in two languages.

---

## Contract Cleanup

### Problem

The app emits a valid full OpenAPI document, but it is not a clean CLI contract:

- it includes many non-CLI routes,
- it contains duplicate or aliasable surfaces that a generated CLI client should not see,
- it uses default FastAPI `operationId` values,
- it emits OpenAPI 3.1 numeric exclusivity fields that break the selected Go generator.

### Deliverables

- Add a dedicated export step that produces `cli/openapi/agent-openapi.json`.
- Filter the schema to the CLI routes only:
  - jobs
  - agent search
  - agent onboarding
  - digest generation
  - content list/detail/submit
  - source list/subscribe
- Rewrite operation identifiers to stable CLI-oriented names.
- Normalize the exported schema to OpenAPI 3.0.3-compatible exclusivity fields for code generation.

### Acceptance Criteria

- The filtered schema is checked in.
- The schema is sufficient to generate the Go client without hand-edited post-processing.
- CLI codegen does not depend on a live running server.

---

## Go Rewrite

### Command Surface

The new CLI is a clean break in ergonomics, but remains pipes-first and machine-friendly:

- `config set server <url>`
- `config set api-key <key>`
- `config show`
- `content list`
- `content get <id>`
- `content submit <url>`
- `content summarize <url>`
- `search <query>`
- `jobs get <id>`
- `jobs wait <id>`
- `sources list`
- `sources add <feed-url> --feed-type <type>`
- `onboarding start`
- `onboarding status <run-id>`
- `onboarding complete <run-id>`
- `digest generate`
- `digest list`
- `completion <shell>`
- `version`

### Runtime Rules

- JSON is the default output.
- All commands return a stable top-level envelope with `ok`, `command`, `data`, and optional `job` or `error`.
- Config precedence is `flags > env > config file`.
- Config remains stored at `~/.config/newsly-agent/config.json`.
- `NEWSLY_AGENT_CONFIG` becomes the canonical env var, with `NEWSLY_AGENT_CONFIG_PATH` kept as a compatibility alias.

### Build Layout

- `cli/` becomes its own Go module.
- Generated client code lives under `cli/internal/api`.
- Hand-written command/runtime code lives under `cli/internal/...` and `cli/cmd/newsly-agent`.

---

## Validation and Tooling

### Tests

- Add Go tests for config loading, envelope rendering, and polling behavior.
- Add command-level tests against an `httptest` server or transport stub.

### CI

- Add Go setup to CI.
- Run `go test ./...` from `cli/`.
- Add a generation freshness check for:
  - `cli/openapi/agent-openapi.json`
  - generated client code

### Documentation

- Rewrite `cli/README.md` for the Go CLI.
- Document how to regenerate the filtered schema and client artifacts.

---

## Cutover

- Remove the Python CLI entrypoint from `pyproject.toml`.
- Delete Python-only CLI tests once the Go CLI covers the supported behavior.
- Keep the backend routes unchanged; this is a client/runtime rewrite, not a server API redesign.
