# Coding Guidelines

## Local Patterns

FastAPI route shape:

```python
@router.get("/items", response_model=ContentListResponse)
def list_news_items(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ContentListResponse:
    return list_visible_news_items(db, user_id=require_user_id(current_user))
```

Structured logging shape:

```python
logger.error(
    "Unable to resolve feed config for content",
    extra={
        "component": "feed_backfill",
        "operation": "resolve_config",
        "item_id": str(content.id),
    },
)
```

Settings shape:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    database_url: PostgresDsn
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
```

## Tests and Checks

- Add tests for new functionality under `tests/` when you change production behavior.
- Scripts under `scripts/` do not need tests unless the task specifically asks for them.
- If you change the admin CLI, bug-test the touched CLI commands with `pytest tests/admin -v` and `ruff check admin tests/admin` before handoff when possible.
- Run `ruff check` on touched Python files, or the repo, before handoff when possible.
- Use `pytest tests/ -v` for relevant validation when behavior changes.

## Common Commands

```bash
uv sync && . .venv/bin/activate
alembic -c migrations/alembic.ini upgrade head
scripts/dev.sh
ruff check .
ruff format .
pytest tests/ -v
uv run -m admin logs exceptions --limit 20
uv run -m admin logs tail --limit 200
```
