# Docs Index

This folder is organized into canonical behavior, durable system references, and historical change context.

- `docs/laws/` — canonical product behavior and invariants
- `docs/architecture.md` — canonical system architecture reference
- `docs/architecture-improvement-plan-2026-04-27.md` — ranked architecture hardening and execution plan
- `docs/coding-guidelines.md` — local code patterns, test expectations, and common commands
- `docs/generate_architecture.sh` — thin Codex wrapper for `docs/architecture.md`
- `scripts/architecture_guard.sh` — targeted guard checks for architecture hardening seams
- `docs/library/` — durable guides, integrations, operations, reference material, deploy docs, and shipped feature notes
  - Start with `docs/library/operations/command-index.md` for script entrypoints
- `docs/initiatives/` — consolidated plans, specs, and research docs organized by initiative

Useful reading commands:

```bash
find docs/laws -type f -name '*.md' | sort | xargs cat
find docs/initiatives -type f -name '*.md' | sort | xargs cat
```

Useful regeneration commands:

```bash
./docs/generate_architecture.sh
```
