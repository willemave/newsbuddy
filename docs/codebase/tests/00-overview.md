# tests/

Source folder: `tests`

## Purpose
Focused Python test suite for backend behavior, admin CLI, generated contracts, scraper/processing flows, and local iOS visual smoke artifacts.

## Runtime behavior
- Tests are organized by production boundary: routers, services, pipeline, models, repositories, queries, scraping, processing strategies, core infrastructure, admin, CLI, contracts, scripts, and integration flows.
- `tests/conftest.py` provides shared fixtures; support code lives in `tests/support`.
- Fixture data and HTML snapshots live under `tests/fixtures`.
- iOS E2E assets live under `tests/ios_e2e` with flow definitions and baseline screenshots.

## Important folders
| Path | Purpose |
|---|---|
| `tests/admin/` | Operator CLI argument/output/remote-operation tests. |
| `tests/application/` | Application command/use-case tests. |
| `tests/cli/` | Python-side tests around CLI-adjacent behavior. |
| `tests/contracts/` | API fixture and contract compatibility checks. |
| `tests/core/` | Settings, auth/deps, logging, observability, storage, and request middleware tests. |
| `tests/http_client/` | Robust HTTP client behavior. |
| `tests/integration/` | Cross-layer API, search, conversion, pipeline, and user-isolation flows. |
| `tests/models/` | Model/metadata contract tests. |
| `tests/pipeline/` | Queue handler, worker, and task behavior. |
| `tests/processing_strategies/` | URL extraction strategy tests. |
| `tests/queries/` | Query adapter/projection tests. |
| `tests/repositories/` | Repository persistence/query tests. |
| `tests/routers/` | FastAPI endpoint tests, including `tests/routers/api`. |
| `tests/scraping/` | Feed, Reddit, podcast, aggregator, and scraper runner tests. |
| `tests/scripts/` | Tests for important maintenance/generation scripts. |
| `tests/services/` | Service-layer business behavior tests. |
| `tests/utils/` | Utility tests. |
| `tests/ios_e2e/` | iOS smoke-test flows and baseline images. |

## Common commands
```bash
uv run pytest tests/ -v
uv run pytest tests/admin -v
uv run ruff check tests
```
