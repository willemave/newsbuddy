"""Integration coverage for exact processing-task claim identity."""

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.core.settings import get_settings
from app.models.db import ProcessingTask
from app.testing.postgres_harness import create_temporary_postgres_harness


def _column_names(engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("processing_tasks")}


def test_lease_token_upgrade_downgrade_and_reupgrade(monkeypatch) -> None:
    harness = create_temporary_postgres_harness(
        schema_prefix="queue_lease_token_migration",
        tables=[ProcessingTask.__table__],
    )
    try:
        with harness.engine.begin() as connection:
            connection.execute(text("ALTER TABLE processing_tasks DROP COLUMN lease_token"))
            connection.execute(
                text(
                    "INSERT INTO processing_tasks "
                    "(task_type, status, queue_name, payload, available_at, retry_count) "
                    "VALUES ('summarize', 'processing', 'content', '{}'::json, NOW(), 0)"
                )
            )
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY NOT NULL)")
            )
            connection.execute(text("INSERT INTO alembic_version VALUES ('20260725_01')"))

        migration_database_url = harness.database_url.replace("%3D", "=")
        monkeypatch.setenv("DATABASE_URL", migration_database_url)
        get_settings.cache_clear()
        config = Config("migrations/alembic.ini")

        command.upgrade(config, "head")
        assert "lease_token" in _column_names(harness.engine)
        with harness.engine.connect() as connection:
            lease_token = connection.execute(
                text("SELECT lease_token FROM processing_tasks")
            ).scalar_one()
            assert lease_token is None

        command.downgrade(config, "20260725_01")
        assert "lease_token" not in _column_names(harness.engine)

        command.upgrade(config, "head")
        assert "lease_token" in _column_names(harness.engine)
    finally:
        get_settings.cache_clear()
        harness.close()
