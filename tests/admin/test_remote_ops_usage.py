"""Tests for usage aggregation and remote log helpers."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from admin.remote_ops import (
    RemoteContext,
    logs_exceptions,
    preview_regenerate_images,
    preview_sanitize_content_metadata,
    regenerate_images,
    sanitize_content_metadata,
    usage_by_content,
    usage_by_user,
    usage_summary,
)
from app.models.schema import Content, ContentStatusEntry, ProcessingTask, VendorUsageRecord
from app.models.user import User
from app.testing.postgres_harness import create_temporary_postgres_harness


@pytest.fixture
def remote_context(tmp_path) -> Iterator[RemoteContext]:
    harness = create_temporary_postgres_harness(
        schema_prefix="newsly_test",
        tables=[
            User.__table__,
            Content.__table__,
            ProcessingTask.__table__,
            VendorUsageRecord.__table__,
        ],
    )
    try:
        with harness.session_factory() as session:
            session.add(
                User(
                    id=1,
                    apple_id="apple-1",
                    email="user@example.com",
                    full_name="User One",
                    is_admin=False,
                    is_active=True,
                )
            )
            session.add(
                Content(
                    id=7,
                    content_type="article",
                    url="https://example.com/article",
                    title="Example Article",
                    status="completed",
                    content_metadata={},
                )
            )
            session.add_all(
                [
                    VendorUsageRecord(
                        provider="openai",
                        model="gpt-5.4-mini",
                        feature="summarization",
                        operation="summarize",
                        source="worker",
                        user_id=1,
                        content_id=7,
                        input_tokens=10,
                        output_tokens=5,
                        total_tokens=15,
                        cost_usd=cast(Any, Decimal("0.12")),
                        currency="USD",
                        pricing_version="2026-03-28",
                        metadata_json={"access_token": "secret"},
                        created_at=datetime(2026, 3, 28, 12, 0, tzinfo=UTC).replace(tzinfo=None),
                    ),
                    VendorUsageRecord(
                        provider="anthropic",
                        model="claude-haiku",
                        feature="summarization",
                        operation="classify",
                        source="worker",
                        user_id=1,
                        content_id=7,
                        input_tokens=6,
                        output_tokens=4,
                        total_tokens=10,
                        cost_usd=cast(Any, Decimal("0.08")),
                        currency="USD",
                        pricing_version="2026-03-28",
                        metadata_json={},
                        created_at=datetime(2026, 3, 28, 12, 5, tzinfo=UTC).replace(tzinfo=None),
                    ),
                ]
            )
            session.commit()
        logs_root = tmp_path / "logs"
        error_logs_dir = logs_root / "errors"
        error_logs_dir.mkdir(parents=True)
        (error_logs_dir / "worker_errors_1.jsonl").write_text(
            "\n".join(
                [
                    (
                        '{"timestamp":"2026-03-30T12:00:00Z","component":"worker",'
                        '"operation":"summarize","error_type":"ValueError",'
                        '"error_message":"new failure"}'
                    ),
                    (
                        '{"timestamp":"2026-03-29T12:00:00Z","component":"worker",'
                        '"operation":"classify","error_type":"RuntimeError",'
                        '"error_message":"older failure"}'
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        yield RemoteContext(
            database_url=harness.database_url,
            logs_dir=logs_root,
            service_log_dir=tmp_path / "service_logs",
        )
    finally:
        harness.close()


def test_usage_summary_groups_by_feature(remote_context):
    summary = usage_summary(remote_context, group_by="feature")

    assert summary["totals"]["call_count"] == 2
    assert summary["totals"]["total_tokens"] == 25
    assert summary["groups"] == [
        {
            "key": "summarization",
            "call_count": 2,
            "input_tokens": 16,
            "output_tokens": 9,
            "total_tokens": 25,
            "request_count": 0,
            "resource_count": 0,
            "cost_usd": 0.2,
        }
    ]


def test_usage_summary_includes_unit_metered_vendor_costs(remote_context):
    engine = create_engine(remote_context.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as session:
            session.add_all(
                [
                    VendorUsageRecord(
                        provider="exa",
                        model="search",
                        feature="external_search",
                        operation="exa.search",
                        source="chat",
                        request_count=1,
                        resource_count=8,
                        cost_usd=cast(Any, Decimal("0.28072")),
                        currency="USD",
                        pricing_version="2026-03-28",
                        metadata_json={},
                        created_at=datetime(2026, 3, 28, 13, 0, tzinfo=UTC).replace(tzinfo=None),
                    ),
                    VendorUsageRecord(
                        provider="x",
                        model="posts.read",
                        feature="x_api",
                        operation="x_api.list_tweets",
                        source="scraper",
                        request_count=1,
                        resource_count=100,
                        cost_usd=cast(Any, Decimal("0.5")),
                        currency="USD",
                        pricing_version="2026-03-28",
                        metadata_json={},
                        created_at=datetime(2026, 3, 28, 13, 5, tzinfo=UTC).replace(tzinfo=None),
                    ),
                ]
            )
            session.commit()

        summary = usage_summary(remote_context, group_by="vendor")

        groups = {group["key"]: group for group in summary["groups"]}
        assert groups["exa"]["request_count"] == 1
        assert groups["exa"]["resource_count"] == 8
        assert groups["exa"]["cost_usd"] == 0.28072
        assert groups["x"]["request_count"] == 1
        assert groups["x"]["resource_count"] == 100
        assert groups["x"]["cost_usd"] == 0.5
        assert summary["totals"]["request_count"] == 2
        assert summary["totals"]["resource_count"] == 108
    finally:
        engine.dispose()


def test_usage_by_user_redacts_metadata(remote_context):
    result = usage_by_user(remote_context, user_id=1)

    assert result["user"]["email"] == "user@example.com"
    assert result["totals"]["call_count"] == 2
    assert any(row["metadata"].get("access_token") == "<redacted>" for row in result["rows"])


def test_usage_by_content_includes_content_metadata(remote_context):
    result = usage_by_content(remote_context, content_id=7)

    assert result["content"]["url"] == "https://example.com/article"
    assert result["totals"]["total_tokens"] == 25


def test_logs_exceptions_returns_most_recent_error_records(remote_context):
    result = logs_exceptions(remote_context, limit=1)

    assert result["available"] == 2
    assert result["returned"] == 1
    assert result["exceptions"][0]["error_message"] == "new failure"


def test_logs_exceptions_filters_by_operation(remote_context):
    result = logs_exceptions(remote_context, operation="classify", limit=10)

    assert result["returned"] == 1
    assert result["exceptions"][0]["operation"] == "classify"


def test_logs_exceptions_does_not_require_schema_models(remote_context, monkeypatch):
    def _unexpected_schema_load():
        raise AssertionError("schema models should not load for log-only commands")

    monkeypatch.setattr("admin.remote_ops._load_schema_models", _unexpected_schema_load)

    result = logs_exceptions(remote_context, limit=1)

    assert result["returned"] == 1


def test_preview_sanitize_content_metadata_returns_matching_rows(remote_context):
    harness = create_temporary_postgres_harness(
        schema_prefix="newsly_fix_preview",
        tables=[Content.__table__],
    )
    try:
        malformed_metadata = '{"summary":{"quotes":[{"text":"You\\u0000re not trying to help"}]}}'
        with harness.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO contents (
                    id, content_type, url, title, status, is_aggregate, content_metadata, created_at
                ) VALUES (
                    101, 'article', 'https://example.com/bad', 'Broken Metadata Row',
                    'completed', false, CAST(%s AS json), now()
                )
                """,
                (malformed_metadata,),
            )

        result = preview_sanitize_content_metadata(
            RemoteContext(
                database_url=harness.database_url,
                logs_dir=remote_context.logs_dir,
                service_log_dir=remote_context.service_log_dir,
            ),
            content_id=None,
            limit=10,
        )

        assert result["applied"] is False
        assert result["matched_total"] == 1
        assert result["selected_count"] == 1
        assert result["rows"][0]["id"] == 101
        assert result["rows"][0]["changed"] is True
    finally:
        harness.close()


def test_sanitize_content_metadata_updates_row(remote_context):
    harness = create_temporary_postgres_harness(
        schema_prefix="newsly_fix_apply",
        tables=[Content.__table__],
    )
    try:
        malformed_metadata = '{"summary":{"quotes":[{"text":"You\\u0000re not trying to help"}]}}'
        with harness.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO contents (
                    id, content_type, url, title, status, is_aggregate, content_metadata, created_at
                ) VALUES (
                    102, 'article', 'https://example.com/fix', 'Needs Repair',
                    'completed', false, CAST(%s AS json), now()
                )
                """,
                (malformed_metadata,),
            )

        repair_context = RemoteContext(
            database_url=harness.database_url,
            logs_dir=remote_context.logs_dir,
            service_log_dir=remote_context.service_log_dir,
        )
        result = sanitize_content_metadata(repair_context, content_id=102, limit=10)

        assert result["applied"] is True
        assert result["updated_count"] == 1

        query_result = preview_sanitize_content_metadata(repair_context, content_id=102, limit=10)
        assert query_result["matched_total"] == 0
        assert query_result["rows"] == []
    finally:
        harness.close()


def test_preview_regenerate_images_returns_failed_candidates(remote_context):
    harness = create_temporary_postgres_harness(
        schema_prefix="newsly_fix_regen_preview",
        tables=[Content.__table__, ContentStatusEntry.__table__, ProcessingTask.__table__],
    )
    try:
        with harness.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO contents (
                    id, content_type, url, title, status, is_aggregate, content_metadata, created_at
                ) VALUES (
                    201, 'podcast', 'https://example.com/podcast', 'Needs Image',
                    'completed', false, CAST(%s AS json), now()
                )
                """,
                ('{"summary":{"title":"x"}}',),
            )
            connection.exec_driver_sql(
                """
                INSERT INTO content_status (user_id, content_id, status, created_at)
                VALUES (1, 201, 'inbox', now())
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO processing_tasks (
                    id, task_type, content_id, payload,
                    status, queue_name, created_at, available_at, retry_count, error_message
                ) VALUES (
                    9001, 'generate_image', 201, '{}'::json, 'failed', 'image',
                    now(), now(), 0, 'boom'
                )
                """
            )

        result = preview_regenerate_images(
            RemoteContext(
                database_url=harness.database_url,
                logs_dir=remote_context.logs_dir,
                service_log_dir=remote_context.service_log_dir,
            ),
            content_ids=None,
            limit=10,
        )

        assert result["applied"] is False
        assert result["matched_total"] == 1
        assert result["selected_count"] == 1
        assert result["rows"][0]["content_id"] == 201
        assert result["rows"][0]["eligible"] is True
        assert result["rows"][0]["latest_task_status"] == "failed"
    finally:
        harness.close()


def test_regenerate_images_creates_completed_task_and_updates_metadata(
    remote_context,
    monkeypatch,
    tmp_path,
):
    harness = create_temporary_postgres_harness(
        schema_prefix="newsly_fix_regen_apply",
        tables=[Content.__table__, ContentStatusEntry.__table__, ProcessingTask.__table__],
    )
    try:
        with harness.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO contents (
                    id, content_type, url, title, status, is_aggregate, content_metadata, created_at
                ) VALUES (
                    202, 'podcast', 'https://example.com/podcast-2', 'Repair Me',
                    'completed', false, CAST(%s AS json), now()
                )
                """,
                ('{"summary":{"title":"x"}}',),
            )
            connection.exec_driver_sql(
                """
                INSERT INTO content_status (user_id, content_id, status, created_at)
                VALUES (1, 202, 'inbox', now())
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO processing_tasks (
                    id, task_type, content_id, payload,
                    status, queue_name, created_at, available_at, retry_count, error_message
                ) VALUES (
                    9002, 'generate_image', 202, '{}'::json, 'failed', 'image',
                    now(), now(), 0, 'boom'
                )
                """
            )

        image_path = tmp_path / "202.png"
        thumb_path = tmp_path / "202-thumb.png"
        image_path.write_bytes(b"png")
        thumb_path.write_bytes(b"thumb")

        class _FakeResult:
            def __init__(self) -> None:
                self.success = True
                self.error_message = None
                self.image_path = image_path
                self.thumbnail_path = thumb_path

        class _FakeService:
            infographic_provider = "google"

            def generate_image(self, _content):
                return _FakeResult()

        monkeypatch.setattr(
            "admin.remote_ops.get_image_generation_service",
            lambda: _FakeService(),
        )
        monkeypatch.setattr("admin.remote_ops.content_to_domain", lambda content: content)
        monkeypatch.setattr(
            "admin.remote_ops.build_content_image_url",
            lambda content_id: f"/images/{content_id}.png",
        )
        monkeypatch.setattr(
            "admin.remote_ops.build_thumbnail_url",
            lambda content_id: f"/thumbs/{content_id}.png",
        )

        result = regenerate_images(
            RemoteContext(
                database_url=harness.database_url,
                logs_dir=remote_context.logs_dir,
                service_log_dir=remote_context.service_log_dir,
            ),
            content_ids=[202],
            limit=10,
        )

        assert result["applied"] is True
        assert result["updated_count"] == 1
        assert result["results"][0]["status"] == "completed"

        with harness.engine.begin() as connection:
            row = (
                connection.exec_driver_sql(
                    """
                SELECT
                    content_metadata::jsonb ->> 'image_url' AS image_url,
                    content_metadata::jsonb ->> 'thumbnail_url' AS thumbnail_url,
                    content_metadata::jsonb ? 'image_generated_at' AS has_generated_at
                FROM contents
                WHERE id = 202
                """
                )
                .mappings()
                .one()
            )
            tasks = connection.exec_driver_sql(
                """
                SELECT status
                FROM processing_tasks
                WHERE content_id = 202
                ORDER BY id
                """
            ).fetchall()

            assert row["image_url"] == "/images/202.png"
            assert row["thumbnail_url"] == "/thumbs/202.png"
            assert row["has_generated_at"] is True
        assert sorted(task[0] for task in tasks) == ["completed", "failed"]
    finally:
        harness.close()
