# Codebase Reference

Folder-by-folder reference for the main source-bearing areas of this repository. These pages are meant for fast orientation: what a folder owns, which files matter, how it connects to the rest of the system, and any generated artifacts or runtime dependencies.

## Layout
- `app/` documents the FastAPI backend one top-level folder at a time.
- `admin/` documents the local production-operator CLI.
- `cli/` documents the Go command-line client one top-level folder at a time.
- `client/` documents the SwiftUI iOS app one top-level folder at a time.
- `config/` remains a support section for shared file-backed configuration.
- `docker/` documents the staging/production container entrypoints.
- `migrations/` documents the Alembic migration tree.
- `scripts/` documents local developer, generation, eval, and maintenance scripts.
- `tests/` documents the backend, CLI, contract, and iOS smoke-test suites.

Runtime and generated folders such as `data/`, `db/`, `logs/`, `outputs/`, `.tmp/`, caches, and build products are intentionally excluded.

## Generation workflow
Use `./docs/generate_codebase_docs.sh` from the repo root. It runs Codex with `gpt-5.6-luna` once per documented top-level folder and refreshes only the corresponding `00-overview.md` files.

```bash
./docs/generate_codebase_docs.sh
```

Detailed subfolder pages are maintained separately and should be checked against the current source tree before handoff.

## Markdown shape
- What the folder owns
- Which files or subfolders matter most
- How it fits into the rest of the codebase
- Any generated artifacts, build steps, or runtime dependencies

## Concat commands
```bash
find docs/codebase/app -type f -name '*.md' | sort | xargs cat
find docs/codebase/admin -type f -name '*.md' | sort | xargs cat
find docs/codebase/cli -type f -name '*.md' | sort | xargs cat
find docs/codebase/client -type f -name '*.md' | sort | xargs cat
find docs/codebase/config -type f -name '*.md' | sort | xargs cat
find docs/codebase/docker -type f -name '*.md' | sort | xargs cat
find docs/codebase/migrations -type f -name '*.md' | sort | xargs cat
find docs/codebase/scripts -type f -name '*.md' | sort | xargs cat
find docs/codebase/tests -type f -name '*.md' | sort | xargs cat
find docs/codebase -type f -name '*.md' | sort | xargs cat
```
