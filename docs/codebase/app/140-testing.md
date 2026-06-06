# app/testing/

Source folder: `app/testing`

## Purpose
Backend-only helpers for test harnesses that need app-owned runtime setup.

## Runtime behavior
- `postgres_harness.py` provides PostgreSQL harness utilities for integration-style tests and local test orchestration.
- This package is not part of production request handling.

## Important files
| File | Purpose |
|---|---|
| `postgres_harness.py` | Postgres lifecycle/connection helpers for tests. |

## Integration points
- Tests under `tests/` should prefer these helpers over duplicating DB harness setup.
