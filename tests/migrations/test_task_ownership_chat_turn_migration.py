"""Integration coverage for task ownership and durable chat-turn migrations."""

import time
from concurrent.futures import ThreadPoolExecutor

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

import app.models.db  # noqa: F401 - register the complete current metadata
from app.core.settings import get_settings
from app.testing.postgres_harness import create_temporary_postgres_harness


def _drop_vendor_usage_user_foreign_key(engine) -> None:
    foreign_keys = inspect(engine).get_foreign_keys("vendor_usage_records")
    constraint_name = next(
        foreign_key["name"]
        for foreign_key in foreign_keys
        if foreign_key["constrained_columns"] == ["user_id"]
    )
    with engine.begin() as connection:
        connection.execute(
            text(f'ALTER TABLE vendor_usage_records DROP CONSTRAINT "{constraint_name}"')
        )


def _prepare_pre_task_ownership_schema(engine) -> None:
    _drop_vendor_usage_user_foreign_key(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE chat_messages DROP COLUMN processing_context"))
        connection.execute(text("ALTER TABLE chat_messages DROP COLUMN tool_progress_updated_at"))
        connection.execute(text("ALTER TABLE chat_messages DROP COLUMN tool_progress_revision"))
        connection.execute(text("ALTER TABLE chat_messages DROP COLUMN tool_progress"))
        connection.execute(text("ALTER TABLE chat_messages DROP COLUMN deep_research_response_id"))
        connection.execute(text("ALTER TABLE chat_messages DROP COLUMN stream_updated_at"))
        connection.execute(text("ALTER TABLE chat_messages DROP COLUMN stream_revision"))
        connection.execute(text("ALTER TABLE chat_messages DROP COLUMN stream_generation"))
        connection.execute(text("ALTER TABLE chat_messages DROP COLUMN partial_text"))
        connection.execute(text("ALTER TABLE briefing_segments DROP COLUMN event_groups"))
        connection.execute(text("DROP TABLE agent_data_files"))
        connection.execute(text("DROP TABLE agent_vm_system_state"))
        connection.execute(text("ALTER TABLE users DROP COLUMN agent_data_revision"))
        connection.execute(
            text("ALTER TABLE users DROP COLUMN agent_vm_snapshot_template_revision")
        )
        connection.execute(text("ALTER TABLE users DROP COLUMN agent_vm_snapshot_id"))
        connection.execute(text("ALTER TABLE users DROP COLUMN agent_vm_template_revision"))
        connection.execute(text("ALTER TABLE users DROP COLUMN agent_vm_sandbox_id"))
        connection.execute(text("DROP TABLE processing_task_user_access"))
        connection.execute(text("DROP TABLE consumed_refresh_tokens"))
        connection.execute(text("DROP INDEX ix_processing_tasks_owner_user_id"))
        connection.execute(text("ALTER TABLE processing_tasks DROP COLUMN owner_user_id"))
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('20260730_02')"))


def _wait_for_vendor_usage_ddl_lock(harness, *, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with harness.engine.connect() as connection:
            waiting = connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_locks AS lock
                        JOIN pg_class AS relation ON relation.oid = lock.relation
                        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = current_schema()
                          AND relation.relname = 'vendor_usage_records'
                          AND NOT lock.granted
                    )
                    """
                ),
            ).scalar_one()
        if waiting:
            return True
        time.sleep(0.01)
    return False


def test_task_ownership_and_chat_context_upgrade_and_downgrade(monkeypatch) -> None:
    harness = create_temporary_postgres_harness(schema_prefix="task_ownership_chat_turn_migration")
    try:
        _prepare_pre_task_ownership_schema(harness.engine)
        with harness.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, apple_id, email, is_admin, is_active, "
                    "has_completed_new_user_tutorial, has_completed_onboarding, "
                    "reading_experience) VALUES "
                    "(1, 'migration-user-1', 'migration-1@example.com', false, true, "
                    "false, false, 'briefing'), "
                    "(2, 'migration-user-2', 'migration-2@example.com', false, true, "
                    "false, false, 'briefing')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO contents "
                    "(id, content_type, url, is_aggregate, status, content_metadata, created_at) "
                    "VALUES (10, 'article', 'https://example.com/migration', false, 'new', "
                    "'{}'::json, NOW())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO content_status "
                    "(user_id, content_id, status, created_at) "
                    "VALUES (2, 10, 'inbox', NOW()), "
                    "(999, 10, 'inbox', NOW())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO audio_episodes "
                    "(id, user_id, kind, status, title, input_hash, source_item_ids, "
                    "source_snapshot, prompt_version, audio_content_type, share_enabled, "
                    "created_at, updated_at) VALUES "
                    "(301, 1, 'fast_news_digest', 'pending', 'Legacy episode', "
                    "'legacy-audio-episode', '[]'::jsonb, '{}'::jsonb, 1, "
                    "'audio/mpeg', false, NOW(), NOW())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO processing_tasks "
                    "(id, task_type, content_id, payload, status, queue_name, "
                    "available_at, retry_count) VALUES "
                    "(101, 'discover_feeds', NULL, '{\"user_id\": 1}'::json, "
                    "'pending', 'feed_discovery', NOW(), 0), "
                    "(102, 'summarize', 10, '{}'::json, 'pending', 'content', NOW(), 0), "
                    "(103, 'generate_audio_episode', NULL, "
                    "'{\"audio_episode_id\": 301}'::json, "
                    "'pending', 'audio_episode', NOW(), 0), "
                    "(104, 'discover_feeds', NULL, "
                    '\'{"user_id": "999999999999999999999999999999999999999"}\'::json, '
                    "'pending', 'feed_discovery', NOW(), 0), "
                    "(105, 'generate_audio_episode', NULL, "
                    '\'{"audio_episode_id": "999999999999999999999999999999999999999"}\'::json, '
                    "'pending', 'audio_episode', NOW(), 0)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO vendor_usage_records "
                    "(provider, model, feature, operation, user_id, currency, "
                    "metadata, created_at) "
                    "VALUES ('test', 'test', 'migration', 'migration', 999, 'USD', "
                    "'{}'::json, NOW())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO chat_messages "
                    "(id, session_id, message_list, created_at, status) "
                    "VALUES (201, 42, '[]', NOW(), 'processing')"
                )
            )

        monkeypatch.setenv("DATABASE_URL", harness.database_url.replace("%3D", "="))
        get_settings.cache_clear()
        config = Config("migrations/alembic.ini")
        command.upgrade(config, "head")

        inspector = inspect(harness.engine)
        assert "owner_user_id" in {
            column["name"] for column in inspector.get_columns("processing_tasks")
        }
        assert "processing_context" in {
            column["name"] for column in inspector.get_columns("chat_messages")
        }
        assert inspector.has_table("processing_task_user_access")
        assert inspector.has_table("consumed_refresh_tokens")

        with harness.engine.connect() as connection:
            owners: dict[int, int | None] = {
                int(task_id): int(owner_user_id) if owner_user_id is not None else None
                for task_id, owner_user_id in connection.execute(
                    text("SELECT id, owner_user_id FROM processing_tasks ORDER BY id")
                ).tuples()
            }
            assert owners == {101: 1, 102: None, 103: 1, 104: None, 105: None}
            audio_payload = connection.execute(
                text("SELECT payload FROM processing_tasks WHERE id = 103")
            ).scalar_one()
            assert audio_payload == {"audio_episode_id": 301, "user_id": 1}
            access = set(
                connection.execute(
                    text(
                        "SELECT task_id, user_id FROM processing_task_user_access "
                        "ORDER BY task_id, user_id"
                    )
                ).all()
            )
            assert access == {(101, 1), (102, 2), (103, 1)}
            assert (
                connection.execute(text("SELECT user_id FROM vendor_usage_records")).scalar_one()
                is None
            )
            chat_state = connection.execute(
                text("SELECT status, error FROM chat_messages WHERE id = 201")
            ).one()
            assert chat_state == (
                "failed",
                "This message was interrupted by an app update. Please retry.",
            )
            vendor_constraint_validated = connection.execute(
                text(
                    "SELECT convalidated FROM pg_constraint "
                    "WHERE conname = 'fk_vendor_usage_records_user_id_users' "
                    "AND conrelid = 'vendor_usage_records'::regclass"
                )
            ).scalar_one()
            assert vendor_constraint_validated is True

        vendor_foreign_keys = inspect(harness.engine).get_foreign_keys("vendor_usage_records")
        assert any(
            foreign_key["constrained_columns"] == ["user_id"]
            and foreign_key["referred_table"] == "users"
            and foreign_key["options"].get("ondelete") == "CASCADE"
            for foreign_key in vendor_foreign_keys
        )

        command.downgrade(config, "20260730_02")
        inspector = inspect(harness.engine)
        assert "owner_user_id" not in {
            column["name"] for column in inspector.get_columns("processing_tasks")
        }
        assert "processing_context" not in {
            column["name"] for column in inspector.get_columns("chat_messages")
        }
        assert not inspector.has_table("processing_task_user_access")
        assert not inspector.has_table("consumed_refresh_tokens")
    finally:
        get_settings.cache_clear()
        harness.close()


def test_task_ownership_migration_fences_concurrent_vendor_usage_orphan(monkeypatch) -> None:
    harness = create_temporary_postgres_harness(
        schema_prefix="task_ownership_concurrent_vendor_usage"
    )
    race_connection = None
    race_transaction = None
    try:
        _prepare_pre_task_ownership_schema(harness.engine)
        monkeypatch.setenv("DATABASE_URL", harness.database_url.replace("%3D", "="))
        get_settings.cache_clear()

        race_connection = harness.engine.connect()
        race_transaction = race_connection.begin()
        race_connection.execute(
            text(
                "INSERT INTO vendor_usage_records "
                "(provider, model, feature, operation, user_id, currency, metadata, created_at) "
                "VALUES ('test', 'test', 'migration-race', 'migration-race', 999, "
                "'USD', '{}'::json, NOW())"
            )
        )

        config = Config("migrations/alembic.ini")
        with ThreadPoolExecutor(max_workers=1) as executor:
            migration = executor.submit(command.upgrade, config, "20260807_01")
            migration_waited_for_writer = _wait_for_vendor_usage_ddl_lock(harness)
            if race_transaction.is_active:
                race_transaction.commit()
            assert migration_waited_for_writer
            migration.result(timeout=10)

        with harness.engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT user_id FROM vendor_usage_records "
                        "WHERE operation = 'migration-race'"
                    )
                ).scalar_one()
                is None
            )
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("20260807_01")
            assert (
                connection.execute(
                    text(
                        "SELECT convalidated FROM pg_constraint "
                        "WHERE conname = 'fk_vendor_usage_records_user_id_users' "
                        "AND conrelid = 'vendor_usage_records'::regclass"
                    )
                ).scalar_one()
                is True
            )
    finally:
        if race_transaction is not None and race_transaction.is_active:
            race_transaction.rollback()
        if race_connection is not None:
            race_connection.close()
        get_settings.cache_clear()
        harness.close()
