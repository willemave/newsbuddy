# Codebase Reference

Generated folder-by-folder reference for the backend (`app/`), iOS client (`client/`), and runtime configuration (`config/`).

## Layout
- `app/` documents the FastAPI backend, pipeline, scrapers, and services.
- `client/` documents the SwiftUI app, extension, services, view models, and supporting project files.
- `config/` documents file-backed feed and tooling configuration.

## Concat commands
```bash
find docs/codebase/app -type f -name '*.md' | sort | xargs cat
find docs/codebase/client -type f -name '*.md' | sort | xargs cat
find docs/codebase/config -type f -name '*.md' | sort | xargs cat
find docs/codebase -type f -name '*.md' | sort | xargs cat
```

## Regeneration
```bash
uv run python scripts/generate_codebase_docs.py
```
