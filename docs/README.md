# Docs Index

This folder is organized into durable reference docs plus concat-friendly generated references and initiative history.

- `docs/architecture.md` — canonical system architecture reference
- `docs/architecture-improvement-plan-2026-04-27.md` — ranked architecture hardening and execution plan
- `docs/codebase/` — Codex-generated folder-by-folder codebase reference for `app/`, `cli/`, and `client/`, plus a small `config/` support section
- `docs/generate_codebase_docs.sh` — thin Codex wrapper for the codebase overview docs
- `docs/generate_architecture.sh` — thin Codex wrapper for `docs/architecture.md`
- `scripts/architecture_guard.sh` — targeted guard checks for architecture hardening seams
- `docs/library/` — durable guides, integrations, operations, reference material, deploy docs, and shipped feature notes
  - Start with `docs/library/operations/command-index.md` for script entrypoints
- `docs/initiatives/` — consolidated plans, specs, and research docs organized by initiative

Useful concat commands:

```bash
find docs/codebase -type f -name '*.md' | sort | xargs cat
find docs/codebase/cli -type f -name '*.md' | sort | xargs cat
find docs/initiatives -type f -name '*.md' | sort | xargs cat
```

Useful regeneration commands:

```bash
./docs/generate_codebase_docs.sh
./docs/generate_architecture.sh
```
