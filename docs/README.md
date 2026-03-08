# Docs Index

This folder is organized into durable reference docs plus concat-friendly generated references and initiative history.

- `docs/architecture.md` — canonical system architecture reference
- `docs/codebase/` — generated folder-by-folder codebase reference for `app/`, `client/`, and `config/`
- `docs/library/` — durable guides, integrations, operations, reference material, deploy docs, and shipped feature notes
  - Start with `docs/library/operations/command-index.md` for script entrypoints
- `docs/initiatives/` — consolidated plans, specs, and research docs organized by initiative

Useful concat commands:

```bash
find docs/codebase -type f -name '*.md' | sort | xargs cat
find docs/initiatives -type f -name '*.md' | sort | xargs cat
```
