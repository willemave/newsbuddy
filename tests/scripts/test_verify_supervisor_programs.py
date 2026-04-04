"""Tests for supervisor program status helpers."""

from scripts.verify_supervisor_programs import (
    list_missing_running_programs,
    parse_supervisor_status,
    resolve_program_names,
)

SUPERVISOR_STATUS_OUTPUT = """
news_app_server                                       RUNNING   pid 1312452, uptime 6:38:32
news_app_workers_content:news_app_workers_content_1   RUNNING   pid 1312536, uptime 6:38:18
news_app_workers_image                                STOPPED   Apr 02 07:04 PM
news_app_workers_media:news_app_workers_media_1       RUNNING   pid 1274897, uptime 1 day, 8:28:50
news_app_queue_watchdog                               RUNNING   pid 1274896, uptime 1 day, 8:28:50
""".strip()


def test_parse_supervisor_status_extracts_name_and_status() -> None:
    """Status parsing should keep only supervisor program names and statuses."""
    assert parse_supervisor_status(SUPERVISOR_STATUS_OUTPUT) == [
        ("news_app_server", "RUNNING"),
        ("news_app_workers_content:news_app_workers_content_1", "RUNNING"),
        ("news_app_workers_image", "STOPPED"),
        ("news_app_workers_media:news_app_workers_media_1", "RUNNING"),
        ("news_app_queue_watchdog", "RUNNING"),
    ]


def test_resolve_program_names_matches_group_entries_by_prefix() -> None:
    """Configured group names should resolve to actual supervisor member entries."""
    assert resolve_program_names(
        [
            "news_app_server",
            "news_app_workers_content",
            "news_app_workers_image",
            "news_app_workers_media",
            "news_app_queue_watchdog",
        ],
        SUPERVISOR_STATUS_OUTPUT,
    ) == [
        "news_app_server",
        "news_app_workers_content:news_app_workers_content_1",
        "news_app_workers_image",
        "news_app_workers_media:news_app_workers_media_1",
        "news_app_queue_watchdog",
    ]


def test_list_missing_running_programs_reports_stopped_and_missing_programs() -> None:
    """Required programs should fail when stopped or absent."""
    assert list_missing_running_programs(
        [
            "news_app_server",
            "news_app_workers_content",
            "news_app_workers_image",
            "news_app_workers_twitter",
        ],
        SUPERVISOR_STATUS_OUTPUT,
    ) == [
        "news_app_workers_image",
        "news_app_workers_twitter",
    ]
