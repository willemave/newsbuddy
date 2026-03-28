# Newsly Development Guide

> For comprehensive technical documentation (database schema, API endpoints, Pydantic schemas, project structure), see **[docs/architecture.md](docs/architecture.md)**.

## Project Overview

Newsly is a FastAPI-based content ingestion and summarization app. It accepts user-submitted URLs, classifies and processes articles, podcasts, and news digests through background workers, generates summaries and images, and also includes an iOS client/share extension.

## Key Commands

```bash
uv sync && . .venv/bin/activate
alembic upgrade head
scripts/start_server.sh
scripts/start_workers.sh
scripts/start_scrapers.sh
ruff check .
ruff format .
pytest tests/ -v
```

## Important Files

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app entry |
| `app/core/settings.py` | Configuration |
| `app/core/db.py` | Database setup |
| `app/models/schema.py` | ORM models |
| `app/services/content_analyzer.py` | URL analysis and content classification |
| `app/services/feed_detection.py` | RSS/Atom feed detection and classification |
| `app/services/image_generation.py` | AI image and thumbnail generation |
| `scripts/run_workers.py` | Worker entry point |
| `scripts/run_scrapers.py` | Scraper entry point |
| `client/newsly/ShareExtension/ShareViewController.swift` | iOS share extension entry |

## Architecture Docs

See **[docs/architecture.md](docs/architecture.md)** for the full project structure, schema, API, and operational details.

---

## 1. Python / FastAPI Coding Rules

* **Functions over classes**.
* **Full type hints**; validate with **Pydantic v2** models. Use `typing` for complex types.
* **RORO** pattern (receive object, return object).
* `lower_snake_case` for files/dirs; verbs in variables (`is_valid`, `has_permission`).
* Guard-clause error handling; early returns over nested `else`.
* **Docstrings**: Use Google-style for all public functions/classes.
* **Constants**: Define in `app/constants.py` or module-level UPPER_CASE.

---

## 2. FastAPI Best Practices

* Use **lifespan** context, not `startup/shutdown` events.
* Inject DB/session with dependencies; use `Annotated` for cleaner signatures.
* Middleware order matters: logging → tracing → CORS → error capture.

---

## 3. Code Quality & Safety

* **No hardcoded secrets**; use `pydantic-settings` for config management.
* **Input validation**: Always validate at boundaries (API, external services).
* **SQL injection prevention**: Use parameterized queries, never f-strings.
* **Graceful degradation**: Circuit breakers for external services.
* **Error logging**: Use `logger.error()` or `logger.exception()` directly with structured `extra` fields (see below).

### Error Logging Convention

Use `logger.error()` or `logger.exception()` directly with structured `extra` fields:

Standard `extra` fields:
- `component`: Module/worker name (e.g., `"content_worker"`, `"http_service"`)
- `operation`: Operation name (e.g., `"summarize"`, `"http_fetch"`)
- `item_id`: ID of item being processed (optional)
- `context_data`: Dict with additional context (optional)

Errors at level ERROR+ are automatically written to JSONL files in `logs/errors/`.

---

## 4. Testing Requirements

* **Write tests for all new functionality** in `tests/` using idiomatic pytest.
* **Scripts in `scripts/` do not require tests** unless explicitly requested.
* Test structure mirrors app structure: `tests/routers/`, `tests/services/`, etc.
* Test file naming: `test_<module_name>.py`.
* **Test categories**:
  - Unit tests: isolated function/class testing
  - Integration tests: API endpoints with test DB
  - Contract tests: external service interactions
* Use pytest fixtures for setup/teardown.
* **TestClient** from FastAPI for endpoint testing.
* Mock external dependencies with `pytest-mock` or `unittest.mock`.
* **Run tests**: `pytest tests/ -v`
* **Test data**: Use factories or fixtures, never production data.

---

## 5. Development Workflow

* **Pre-commit hooks**: `ruff` for linting/formatting
* **Environment management**: `.env.example` template; never commit `.env`. Use `app/core/settings.py` and Pydantic for settings.
* **Database migrations**: Alembic with descriptive revision messages.
* **UI**: Jinja2 templates for HTML pages (not a JavaScript/React app).
* **Error responses**: Consistent format with error codes, messages, details.
* **Tailwind CSS**: Write to `./static/css/styles.css`, build with:
  ```bash
  npx @tailwindcss/cli -i ./static/css/styles.css -o ./static/css/app.css
  ```

### Pipeline Notes
* `POST /api/content/submit` creates `content_type=unknown`, queues `ANALYZE_URL` → `PROCESS_CONTENT`.
* `ANALYZE_URL` uses pattern matching + LLM page analysis to set content type/platform/media.
* `SUMMARIZE` writes interleaved summaries for articles/podcasts, news digests for news, then enqueues `GENERATE_IMAGE`.
* `GENERATE_IMAGE` creates thumbnails/infographics and exposes `image_url`/`thumbnail_url` in API responses.

---

## 6. Package & Dev Tools

### Package Management (uv)
```bash
uv sync                    # Install all dependencies
uv add <package>           # Add dependency
uv add --dev <package>     # Add dev dependency
source .venv/bin/activate  # Activate venv
```

### Database
```bash
alembic upgrade head       # Apply migrations
alembic revision -m "..."  # Create migration
```

### Code Quality
```bash
ruff check .               # Lint
ruff format .              # Format
pytest tests/ -v       # Run tests
```

### Running the App
```bash
# Local development
uv sync && . .venv/bin/activate
alembic upgrade head
scripts/start_server.sh              # API server
scripts/start_workers.sh             # Task workers
scripts/start_scrapers.sh            # Content scrapers
```

---

## 7. Preferred Dev Tools

* **LLM internet search**: Use the EXA MCP `web_search_exa` tool for any web/internet lookups (and `get_code_context_exa` for external API/library docs).
* **Webpage fetching**: You can use your inbuilt webpage fetching tools to resolve web pages in the prompt. 

| Tool | Purpose | Example |
|------|---------|---------|
| **fd** | Fast file finder | `fd -e py foo` |
| **rg** | Fast code search | `rg "TODO"` |
| **ast-grep (sg)** | AST-aware search | `sg -p 'if ($A) { $B }'` |
| **jq** | JSON processor | `cat data.json \| jq '.items'` |
| **fzf** | Fuzzy finder | `history \| fzf` |
| **bat** | Better cat | `bat file.py` |
| **eza** | Modern ls | `eza -l --git` |
| **httpie** | HTTP client | `http GET api/foo` |
| **delta** | Better git diff | `git diff` (with config) |

---

## 8. Quick Reference

### Key Entry Points
| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app entry |
| `app/core/settings.py` | Configuration |
| `app/core/db.py` | Database setup |
| `app/models/schema.py` | ORM models |
| `app/services/content_analyzer.py` | URL analysis (LLM + trafilatura) |
| `app/services/feed_detection.py` | RSS/Atom feed detection + classification |
| `app/services/image_generation.py` | AI image + thumbnail generation |
| `scripts/run_workers.py` | Worker entry |
| `scripts/run_scrapers.py` | Scraper entry |
| `client/newsly/ShareExtension/ShareViewController.swift` | iOS share extension entry |

### Environment Variables (Required)
```bash
DATABASE_URL="sqlite:///./news_app.db"
JWT_SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_urlsafe(32))">
ADMIN_PASSWORD=<secure-password>
```

### Content Metadata + API Fields
* `summary_type=interleaved` for article/podcast summaries.
* User submissions may include `detected_feed` metadata (RSS/Atom classification).
* `image_url` + `thumbnail_url` are returned in content list/detail when available.

### Content Types
- `article` - Web articles, blog posts, papers
- `podcast` - Audio/video episodes
- `news` - Aggregated news items (HN, Techmeme)

### Status Lifecycle
```
new → pending → processing → completed
                    ↓
                  failed → (retry) → processing
                    ↓
                 skipped
```

### iOS Debugger Agent Usage
When asked to build, run, or debug the iOS app, use the `$ios-debugger-agent` skill and follow its MCP workflow:
1) `mcp__XcodeBuildMCP__list_sims` → pick a **Booted** simulator (ask the user to boot one if none).
2) `mcp__XcodeBuildMCP__session-set-defaults` with `projectPath` (or `workspacePath`), `scheme`, and `simulatorId` (optionally `configuration: "Debug"`, `useLatestOS: true`).
3) Build/run with `mcp__XcodeBuildMCP__build_run_sim` (or `launch_app_sim` if already built).
4) For UI checks: `describe_ui` before `tap`/`gesture`; use `screenshot` for visual confirmation.
5) For logs: `start_sim_log_cap` (use `captureConsole: true` if needed) and `stop_sim_log_cap` to summarize.

---

**Keep all replies short, technical, and complete.**

**Never commit or push unless explicitly asked.** Do not auto-commit after completing a task.

**Always run `ruff check` on touched Python files (or the repo) after a set of changes, and fix violations before final handoff whenever possible.**

For detailed documentation on:
- Complete project structure
- Database schema
- API endpoints
- Pydantic schemas
- Operational scripts
- iOS client architecture
- Authentication system

See **[docs/architecture.md](docs/architecture.md)**.
