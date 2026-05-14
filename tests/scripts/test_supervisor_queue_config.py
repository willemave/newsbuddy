"""Tests for production Supervisor queue worker configuration."""

from __future__ import annotations

from configparser import RawConfigParser
from pathlib import Path


def _load_config(config_path: str) -> RawConfigParser:
    parser = RawConfigParser()
    parser.read(Path(__file__).resolve().parents[2] / config_path)
    return parser


def test_supervisor_config_runs_all_queue_partitions() -> None:
    """Production Supervisor config should match the active queue partitions."""
    parser = _load_config("supervisor.conf")
    required_programs = {
        "program:news_app_workers_content": "--queue content",
        "program:news_app_workers_media": "--queue media",
        "program:news_app_workers_audio_episode": "--queue audio_episode",
        "program:news_app_workers_image": "--queue image",
        "program:news_app_workers_onboarding": "--queue onboarding",
        "program:news_app_workers_backfill": "--queue backfill",
        "program:news_app_workers_discussion": "--queue discussion",
        "program:news_app_workers_twitter": "--queue twitter",
        "program:news_app_workers_chat": "--queue chat",
    }

    for section, queue_arg in required_programs.items():
        assert parser.has_section(section)
        assert queue_arg in parser.get(section, "command")


def test_supervisor_config_parallelizes_content_workers() -> None:
    """Content workers should default to the higher parallelism from the plan."""
    parser = _load_config("supervisor.conf")
    assert parser.get("program:news_app_workers_content", "numprocs") == "4"


def test_supervisor_config_does_not_use_old_transcribe_queue() -> None:
    """The old transcribe queue should not be started as a worker partition."""
    parser = _load_config("supervisor.conf")
    assert not parser.has_section("program:news_app_workers_transcribe")
    commands = "\n".join(
        parser.get(section, "command", fallback="") for section in parser.sections()
    )
    assert "--queue transcribe" not in commands


def test_docker_supervisor_config_runs_all_queue_partitions() -> None:
    """Docker Supervisor config should run every active queue partition."""
    parser = _load_config("docker/supervisord.conf")
    required_programs = {
        "program:worker_content": "run-worker.sh content",
        "program:worker_media": "run-worker.sh media",
        "program:worker_audio_episode": "run-worker.sh audio_episode",
        "program:worker_image": "run-worker.sh image",
        "program:worker_onboarding": "run-worker.sh onboarding",
        "program:worker_backfill": "run-worker.sh backfill",
        "program:worker_discussion": "run-worker.sh discussion",
        "program:worker_twitter": "run-worker.sh twitter",
        "program:worker_chat": "run-worker.sh chat",
    }

    for section, queue_arg in required_programs.items():
        assert parser.has_section(section)
        assert queue_arg in parser.get(section, "command")


def test_docker_supervisor_config_parallelizes_content_workers() -> None:
    """Docker content workers should also use the higher planned parallelism."""
    parser = _load_config("docker/supervisord.conf")
    command = parser.get("program:worker_content", "command")

    assert "run-worker.sh content %(process_num)s" in command
    assert parser.get("program:worker_content", "numprocs") == "4"
    assert parser.get("program:worker_content", "numprocs_start") == "1"


def test_docker_supervisor_config_does_not_use_old_transcribe_queue() -> None:
    """The Docker runtime should not start the old transcribe worker partition."""
    parser = _load_config("docker/supervisord.conf")
    assert not parser.has_section("program:worker_transcribe")
    commands = "\n".join(
        parser.get(section, "command", fallback="") for section in parser.sections()
    )
    assert "run-worker.sh transcribe" not in commands
