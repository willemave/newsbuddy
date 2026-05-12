# Vulture Whitelist Setup

**Date:** 2026-05-12  
**Scope:** Goal 3 whitelist and actionable-report configuration.

## What Changed

- Added `vulture_whitelist.py` at the repo root.
- Updated `[tool.vulture]` to scan `app`, `admin`, `scripts`, selected test paths, and the whitelist file.
- Lowered configured `min_confidence` from `100` to `60`.
- Added decorator ignores for FastAPI routes, admin routes, pydantic-ai tools, Pydantic validators/serializers, and pytest fixtures.

## Whitelist Categories

- Alembic revision metadata and migration hook names.
- Pydantic and `BaseSettings` fields, including `model_config`.
- SQLAlchemy mapped fields and ORM-persisted attributes.
- Generated, `TypedDict`, and API-contract fields.
- Public worker/API enum constants.
- pytest globals, fixture parameters, and mock `side_effect`.
- Runtime framework attributes such as `TimedRotatingFileHandler.namer`.

Admin CLI entrypoints are currently direct argparse dispatch references, not dynamic Vulture false positives. `_sync_database` remains visible in the report on purpose so Goal 4 can decide whether to delete it.

## Verification

```bash
uv run vulture
```

Result: exit code `3` with 85 remaining findings. This is expected for Goal 3; these are the actionable Goal 4 candidates.

```bash
uv run vulture --min-confidence 80
```

Result: exit code `0`, no findings.

```bash
uv run ruff check vulture_whitelist.py
```

Result: pass.

## Remaining Finding Shape

| Type | Count |
|---|---:|
| Unused functions | 37 |
| Unused methods | 19 |
| Unused variables | 15 |
| Unused classes | 12 |
| Unused property | 1 |
| Unused attribute | 1 |

Largest remaining path groups:

| Path group | Count |
|---|---:|
| `app/services` | 24 |
| `scripts` | 9 |
| `app/pipeline` | 6 |
| `app/models/domain` | 6 |
| `app/core` | 6 |
| `app/models/api` | 5 |
| `app/utils` | 4 |
| `app/repositories` | 4 |
| `app/processing_strategies` | 4 |
| `app/models/metadata` | 4 |

## Goal 4 Starting Point

Run `uv run vulture` and work from the reported 85-item queue. Because every remaining item is 60% confidence, each deletion still needs a repo-wide `rg` check and a focused test before removal.
