"""Safety checks for automated processing-task retention."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import ProcessingTask
from scripts import queue_control


def _expired_task(now: datetime) -> ProcessingTask:
    return ProcessingTask(
        task_type=TaskType.SUMMARIZE.value,
        status=TaskStatus.COMPLETED.value,
        payload={},
        queue_name=TaskQueue.CONTENT.value,
        created_at=now - timedelta(days=20),
        completed_at=now - timedelta(days=19),
    )


def test_cleanup_terminal_dry_run_reports_without_deleting(db_session, capsys) -> None:
    task = _expired_task(datetime.now().replace(microsecond=0))
    db_session.add(task)
    db_session.commit()
    task_id = task.id

    queue_control.cleanup_terminal_tasks(
        db_session,
        retention_days=14,
        dry_run=True,
        force=False,
    )

    assert db_session.get(ProcessingTask, task_id) is not None
    output = capsys.readouterr().out
    assert "Matched terminal tasks: 1" in output
    assert "Maximum deletions this run: 1 (batch size: 5000)." in output
    assert "Dry run only; no changes applied." in output


def test_cleanup_terminal_refuses_mutation_without_yes(db_session) -> None:
    task = _expired_task(datetime.now().replace(microsecond=0))
    db_session.add(task)
    db_session.commit()
    task_id = task.id

    with pytest.raises(SystemExit, match="without --yes"):
        queue_control.cleanup_terminal_tasks(
            db_session,
            retention_days=14,
            dry_run=False,
            force=False,
        )

    db_session.rollback()
    assert db_session.get(ProcessingTask, task_id) is not None


def test_cleanup_terminal_apply_skips_full_count_and_forwards_bounds(
    monkeypatch,
    capsys,
) -> None:
    session = Mock()
    cleanup = Mock(
        return_value={
            "deleted_count": 250,
            "batch_count": 3,
            "batch_size": 100,
            "max_delete": 250,
            "has_more": True,
        }
    )
    monkeypatch.setattr(queue_control, "cleanup_terminal_tasks_in_session", cleanup)

    queue_control.cleanup_terminal_tasks(
        session,
        retention_days=14,
        batch_size=100,
        max_delete=250,
        dry_run=False,
        force=True,
    )

    session.query.assert_not_called()
    cleanup.assert_called_once()
    assert cleanup.call_args.kwargs["batch_size"] == 100
    assert cleanup.call_args.kwargs["max_delete"] == 250
    output = capsys.readouterr().out
    assert "Deleted terminal tasks: 250 across 3 batches" in output
    assert "more expired terminal tasks remain" in output


def test_cleanup_terminal_parser_exposes_retention_safety_flags() -> None:
    args = queue_control.build_parser().parse_args(
        ["cleanup-terminal", "--retention-days", "21", "--dry-run"]
    )

    assert args.command == "cleanup-terminal"
    assert args.retention_days == 21
    assert args.batch_size == 5000
    assert args.max_delete == 50000
    assert args.dry_run is True
    assert args.yes is False


def test_daily_cron_applies_fourteen_day_terminal_retention() -> None:
    crontab = Path("docker/crontab").read_text()
    cleanup_lines = [line for line in crontab.splitlines() if "cleanup-terminal" in line]

    assert cleanup_lines == [
        "45 4 * * * cd /app && flock -n /tmp/news_app_queue_cleanup.lock "
        "python scripts/queue_control.py cleanup-terminal --retention-days 14 "
        "--batch-size 5000 --max-delete 50000 --yes"
    ]
