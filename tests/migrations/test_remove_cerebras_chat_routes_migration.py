"""Integration coverage for retiring stored Cerebras chat routes."""

from alembic import command
from alembic.config import Config
from sqlalchemy import text

import app.models.db  # noqa: F401 - register current metadata
from app.core.settings import get_settings
from app.testing.postgres_harness import create_temporary_postgres_harness


def test_remove_cerebras_chat_routes_upgrade_retargets_only_retired_sessions(
    monkeypatch,
) -> None:
    harness = create_temporary_postgres_harness(schema_prefix="remove_cerebras_chat_routes")
    try:
        with harness.engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY NOT NULL)")
            )
            connection.execute(text("INSERT INTO alembic_version VALUES ('20260829_01')"))
            connection.execute(
                text(
                    "INSERT INTO chat_sessions "
                    "(id, user_id, llm_model, llm_provider, council_mode, "
                    "is_hidden_from_history, created_at, is_archived) VALUES "
                    "(1, 1, 'cerebras:zai-glm-4.7', 'cerebras', false, false, now(), false), "
                    "(2, 1, 'openai:gpt-5.6-terra', 'openai', false, false, now(), false)"
                )
            )

        monkeypatch.setenv("DATABASE_URL", harness.database_url.replace("%3D", "="))
        get_settings.cache_clear()
        config = Config("migrations/alembic.ini")
        command.upgrade(config, "head")

        with harness.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, llm_provider, llm_model FROM chat_sessions "
                    "WHERE id IN (1, 2) ORDER BY id"
                )
            ).all()
        assert rows == [
            (1, "openai", "openai:gpt-5.6-terra"),
            (2, "openai", "openai:gpt-5.6-terra"),
        ]
    finally:
        get_settings.cache_clear()
        harness.close()
