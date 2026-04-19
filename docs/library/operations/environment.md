# Newsly Development Guide
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
* **Error context**: Include request IDs, user context in error logs.

---

## 5. Development Workflow

* **Pre-commit hooks**: `ruff` for linting/formatting
* **Environment management**: `.env.example` template; never commit `.env`. Use `app/core/settings.py` and Pydantic for settings.
* **Database migrations**: Alembic with descriptive revision messages.
* **Error responses**: Consistent format with error codes, messages, details.
* **Tailwind CSS**: Write to `./static/css/styles.css`, build with:
  ```bash
  npx @tailwindcss/cli -i ./static/css/styles.css -o ./static/css/app.css
  ```

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
alembic -c migrations/alembic.ini upgrade head       # Apply migrations
alembic -c migrations/alembic.ini revision -m "..."  # Create migration
```

### Code Quality
```bash
ruff check .               # Lint
ruff format .              # Format
pytest tests/ -v       # Run tests
```

### Running the App
```bash
# Local development uses native host services plus local PostgreSQL.
# Reserve Docker for staging/production-style runs.
uv sync && . .venv/bin/activate
./scripts/setup_local_postgres.sh
alembic -c migrations/alembic.ini upgrade head
scripts/start_server.sh              # API server
scripts/start_workers.sh             # Task workers
scripts/start_scrapers.sh            # Content scrapers
```

---

## 7. Preferred Dev Tools

* **LLM internet search**: Use the EXA MCP `web_search_exa` tool for any web/internet lookups (and `get_code_context_exa` for external API/library docs).

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


### Environment Variables (Required)
```bash
DATABASE_URL="postgresql+psycopg://newsly:change-me@127.0.0.1:5432/newsly"
JWT_SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_urlsafe(32))">
ADMIN_PASSWORD=<secure-password>
