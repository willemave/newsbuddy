"""Integration coverage for the bounded refresh-token replay migration."""

from datetime import UTC, datetime, timedelta

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

import app.models.db  # noqa: F401 - register current metadata
from app.core.settings import get_settings
from app.testing.postgres_harness import create_temporary_postgres_harness


def test_refresh_token_replay_upgrade_preserves_legacy_rows_and_downgrades(
    monkeypatch,
) -> None:
    harness = create_temporary_postgres_harness(schema_prefix="refresh_replay_migration")
    try:
        with harness.engine.begin() as connection:
            connection.execute(text("DROP INDEX idx_consumed_refresh_tokens_replay_expiry"))
            connection.execute(
                text("ALTER TABLE consumed_refresh_tokens DROP COLUMN replay_expires_at")
            )
            connection.execute(
                text("ALTER TABLE consumed_refresh_tokens DROP COLUMN replay_payload_encrypted")
            )
            connection.execute(text("ALTER TABLE consumed_refresh_tokens DROP COLUMN attempt_id"))
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY NOT NULL)")
            )
            connection.execute(text("INSERT INTO alembic_version VALUES ('20260825_01')"))
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, apple_id, email, is_admin, is_active, "
                    "has_completed_new_user_tutorial, has_completed_onboarding, "
                    "reading_experience) VALUES "
                    "(1, 'refresh-replay-migration', 'refresh-replay@example.com', "
                    "false, true, false, false, 'briefing')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO consumed_refresh_tokens "
                    "(token_hash, user_id, expires_at, consumed_at) "
                    "VALUES (:token_hash, 1, :expires_at, :consumed_at)"
                ),
                {
                    "token_hash": "a" * 64,
                    "expires_at": datetime.now(UTC) + timedelta(days=1),
                    "consumed_at": datetime.now(UTC),
                },
            )

        monkeypatch.setenv("DATABASE_URL", harness.database_url.replace("%3D", "="))
        get_settings.cache_clear()
        config = Config("migrations/alembic.ini")
        command.upgrade(config, "head")

        inspector = inspect(harness.engine)
        columns = {column["name"] for column in inspector.get_columns("consumed_refresh_tokens")}
        assert {"attempt_id", "replay_payload_encrypted", "replay_expires_at"} <= columns
        indexes = {index["name"] for index in inspector.get_indexes("consumed_refresh_tokens")}
        assert "idx_consumed_refresh_tokens_replay_expiry" in indexes
        with harness.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT attempt_id, replay_payload_encrypted, replay_expires_at "
                    "FROM consumed_refresh_tokens WHERE token_hash = :token_hash"
                ),
                {"token_hash": "a" * 64},
            ).one()
            assert row == (None, None, None)

        command.downgrade(config, "20260825_01")
        columns = {
            column["name"]
            for column in inspect(harness.engine).get_columns("consumed_refresh_tokens")
        }
        assert "attempt_id" not in columns
        assert "replay_payload_encrypted" not in columns
        assert "replay_expires_at" not in columns
    finally:
        get_settings.cache_clear()
        harness.close()
