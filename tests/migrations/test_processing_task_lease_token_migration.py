"""Integration coverage for exact processing-task claim identity."""

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.settings import get_settings
from app.models.db import ProcessingTask
from app.models.db.users import User
from app.testing.postgres_harness import create_temporary_postgres_harness


def _column_names(engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("processing_tasks")}


def _check_constraint_names(engine) -> set[str]:
    return {
        constraint["name"]
        for constraint in inspect(engine).get_check_constraints("processing_tasks")
        if constraint["name"] is not None
    }


def test_lease_token_upgrade_downgrade_and_reupgrade(monkeypatch) -> None:
    harness = create_temporary_postgres_harness(
        schema_prefix="queue_lease_token_migration",
        tables=[User.__table__, ProcessingTask.__table__],
    )
    try:
        with harness.engine.begin() as connection:
            connection.execute(text("ALTER TABLE processing_tasks DROP COLUMN lease_token"))
            processing_task_id = connection.execute(
                text(
                    "INSERT INTO processing_tasks "
                    "(task_type, status, queue_name, payload, available_at, retry_count) "
                    "VALUES ('summarize', 'processing', 'content', '{}'::json, NOW(), 0) "
                    "RETURNING id"
                )
            ).scalar_one()
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY NOT NULL)")
            )
            connection.execute(text("INSERT INTO alembic_version VALUES ('20260725_01')"))

        migration_database_url = harness.database_url.replace("%3D", "=")
        monkeypatch.setenv("DATABASE_URL", migration_database_url)
        get_settings.cache_clear()
        config = Config("migrations/alembic.ini")

        command.upgrade(config, "20260730_01")
        assert "lease_token" in _column_names(harness.engine)

        inconsistent_token = uuid4()
        with harness.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE processing_tasks SET locked_by = 'partial-worker', "
                    "lease_token = :lease_token, lease_expires_at = NOW() + INTERVAL '5 minutes' "
                    "WHERE id = :task_id"
                ),
                {"lease_token": inconsistent_token, "task_id": processing_task_id},
            )
            terminal_task_id = connection.execute(
                text(
                    "INSERT INTO processing_tasks "
                    "(task_type, status, queue_name, payload, available_at, retry_count, "
                    "locked_at, locked_by, lease_token, lease_expires_at) VALUES "
                    "('summarize', 'completed', 'content', '{}'::json, NOW(), 0, NOW(), "
                    "'old-worker', :lease_token, NOW() + INTERVAL '5 minutes') RETURNING id"
                ),
                {"lease_token": uuid4()},
            ).scalar_one()

        command.upgrade(config, "20260730_02")
        assert "ck_processing_tasks_lease_token_has_owner" in _check_constraint_names(
            harness.engine
        )
        with harness.engine.connect() as connection:
            repaired_row = (
                connection.execute(
                    text(
                        "SELECT status, locked_at, locked_by, lease_token, lease_expires_at "
                        "FROM processing_tasks WHERE id = :task_id"
                    ),
                    {"task_id": processing_task_id},
                )
                .mappings()
                .one()
            )
            assert dict(repaired_row) == {
                "status": "pending",
                "locked_at": None,
                "locked_by": None,
                "lease_token": None,
                "lease_expires_at": None,
            }
            repaired_terminal_row = (
                connection.execute(
                    text(
                        "SELECT status, locked_at, locked_by, lease_token, lease_expires_at "
                        "FROM processing_tasks WHERE id = :task_id"
                    ),
                    {"task_id": terminal_task_id},
                )
                .mappings()
                .one()
            )
            assert dict(repaired_terminal_row) == {
                "status": "completed",
                "locked_at": None,
                "locked_by": None,
                "lease_token": None,
                "lease_expires_at": None,
            }

        lease_token = uuid4()
        with harness.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE processing_tasks SET status = 'processing', started_at = NOW(), "
                    "locked_at = NOW(), locked_by = 'new-worker', lease_token = :lease_token, "
                    "lease_expires_at = NOW() + INTERVAL '5 minutes' WHERE id = :task_id"
                ),
                {"lease_token": lease_token, "task_id": processing_task_id},
            )

        with pytest.raises(IntegrityError), harness.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE processing_tasks SET status = 'completed', locked_at = NULL, "
                    "locked_by = NULL, lease_expires_at = NULL WHERE id = :task_id"
                ),
                {"task_id": processing_task_id},
            )

        with harness.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE processing_tasks SET status = 'completed', locked_at = NULL, "
                    "locked_by = NULL, lease_token = NULL, lease_expires_at = NULL "
                    "WHERE id = :task_id"
                ),
                {"task_id": processing_task_id},
            )

        command.downgrade(config, "20260725_01")
        assert "lease_token" not in _column_names(harness.engine)

        command.upgrade(config, "20260730_02")
        assert "lease_token" in _column_names(harness.engine)
        assert "ck_processing_tasks_lease_token_has_owner" in _check_constraint_names(
            harness.engine
        )
    finally:
        get_settings.cache_clear()
        harness.close()
