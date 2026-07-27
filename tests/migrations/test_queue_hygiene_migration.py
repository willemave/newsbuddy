"""Integration coverage for the processing-task hygiene migration."""

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.core.settings import get_settings
from app.models.db import ProcessingTask
from app.testing.postgres_harness import create_temporary_postgres_harness


def _index_columns(engine) -> dict[str, tuple[str, ...]]:
    return {
        index["name"]: tuple(index["column_names"])
        for index in inspect(engine).get_indexes("processing_tasks")
    }


def test_queue_hygiene_upgrade_and_downgrade(monkeypatch) -> None:
    harness = create_temporary_postgres_harness(
        schema_prefix="queue_hygiene_migration",
        tables=[ProcessingTask.__table__],
    )
    try:
        with harness.engine.begin() as connection:
            connection.execute(text("DROP INDEX idx_task_status_available"))
            connection.execute(text("DROP INDEX idx_task_queue_status_available"))
            connection.execute(
                text(
                    "CREATE INDEX idx_task_status_available ON processing_tasks "
                    "(status, available_at, retry_count, created_at)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX idx_task_queue_status_available ON processing_tasks "
                    "(queue_name, status, available_at, retry_count, created_at)"
                )
            )
            connection.execute(
                text("ALTER TABLE processing_tasks ALTER COLUMN retry_count DROP NOT NULL")
            )
            connection.execute(
                text(
                    "INSERT INTO processing_tasks "
                    "(task_type, status, queue_name, payload, available_at, retry_count) "
                    "VALUES ('summarize', 'pending', 'content', '{}'::json, NOW(), NULL)"
                )
            )
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY NOT NULL)")
            )
            connection.execute(text("INSERT INTO alembic_version VALUES ('20260719_02')"))

        migration_database_url = harness.database_url.replace("%3D", "=")
        monkeypatch.setenv("DATABASE_URL", migration_database_url)
        get_settings.cache_clear()
        config = Config("migrations/alembic.ini")
        command.upgrade(config, "head")

        columns = {
            column["name"]: column
            for column in inspect(harness.engine).get_columns("processing_tasks")
        }
        assert columns["retry_count"]["nullable"] is False
        with harness.engine.connect() as connection:
            assert (
                connection.execute(text("SELECT retry_count FROM processing_tasks")).scalar_one()
                == 0
            )
        indexes = _index_columns(harness.engine)
        assert indexes["idx_task_status_available"] == (
            "status",
            "retry_count",
            "available_at",
            "created_at",
            "id",
        )
        assert indexes["idx_task_queue_status_available"] == (
            "queue_name",
            "status",
            "retry_count",
            "available_at",
            "created_at",
            "id",
        )

        command.downgrade(config, "20260719_02")

        columns = {
            column["name"]: column
            for column in inspect(harness.engine).get_columns("processing_tasks")
        }
        assert columns["retry_count"]["nullable"] is True
        indexes = _index_columns(harness.engine)
        assert indexes["idx_task_status_available"] == (
            "status",
            "available_at",
            "retry_count",
            "created_at",
        )
        assert indexes["idx_task_queue_status_available"] == (
            "queue_name",
            "status",
            "available_at",
            "retry_count",
            "created_at",
        )
    finally:
        get_settings.cache_clear()
        harness.close()
