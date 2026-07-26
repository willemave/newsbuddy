"""Tests for production Supervisor queue worker configuration."""

from __future__ import annotations

from configparser import RawConfigParser
from pathlib import Path

from app.models.contracts import TaskQueue


def _load_config(config_path: str) -> RawConfigParser:
    parser = RawConfigParser()
    parser.read(Path(__file__).resolve().parents[2] / config_path)
    return parser


EXPECTED_QUEUE_VALUES = {queue.value for queue in TaskQueue}


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
        "program:news_app_workers_learning": "--queue learning",
        "program:news_app_workers_llm": "--queue llm",
    }

    assert set(required_programs) == {
        f"program:news_app_workers_{queue}" for queue in EXPECTED_QUEUE_VALUES
    }

    for section, queue_arg in required_programs.items():
        assert parser.has_section(section)
        assert queue_arg in parser.get(section, "command")


def test_supervisor_config_runs_one_process_per_queue() -> None:
    """Content parallelism comes from threads inside one process, not numprocs."""
    parser = _load_config("supervisor.conf")
    for queue in EXPECTED_QUEUE_VALUES:
        section = f"program:news_app_workers_{queue}"
        assert parser.get(section, "numprocs", fallback="1") == "1", section


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
    parser = _load_config("docker/supervisord.worker-programs.conf")
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
        "program:worker_learning": "run-worker.sh learning",
        "program:worker_llm": "run-worker.sh llm",
    }

    assert set(required_programs) == {f"program:worker_{queue}" for queue in EXPECTED_QUEUE_VALUES}

    for section, queue_arg in required_programs.items():
        assert parser.has_section(section)
        assert queue_arg in parser.get(section, "command")


def test_docker_supervisor_config_runs_one_process_per_queue() -> None:
    """The Docker runtime should also run a single process per queue."""
    parser = _load_config("docker/supervisord.worker-programs.conf")

    assert "run-worker.sh content 1" in parser.get("program:worker_content", "command")
    for queue in EXPECTED_QUEUE_VALUES:
        section = f"program:worker_{queue}"
        assert parser.get(section, "numprocs", fallback="1") == "1", section


def test_docker_supervisor_config_does_not_use_old_transcribe_queue() -> None:
    """The Docker runtime should not start the old transcribe worker partition."""
    parser = _load_config("docker/supervisord.worker-programs.conf")
    assert not parser.has_section("program:worker_transcribe")
    commands = "\n".join(
        parser.get(section, "command", fallback="") for section in parser.sections()
    )
    assert "run-worker.sh transcribe" not in commands


def test_full_and_worker_docker_profiles_share_worker_programs() -> None:
    """Full and split worker runtimes should include one canonical worker graph."""
    for config_path in ("docker/supervisord.conf", "docker/supervisord.workers.conf"):
        parser = _load_config(config_path)
        assert parser.get("include", "files") == ("/app/docker/supervisord.worker-programs.conf")


def test_local_launchers_use_one_process_per_queue() -> None:
    """Local overrides tune threads; they must not multiply threaded processes."""
    project_root = Path(__file__).resolve().parents[2]
    start_services = (project_root / "scripts/start_services.sh").read_text()
    dev_script = (project_root / "scripts/dev.sh").read_text()

    assert 'cmd+=(--threads "${threads}")' in start_services
    assert '--worker-slot "${slot}"' not in start_services
    assert 'seq 1 "$content_worker_procs"' not in dev_script
    assert 'seq 1 "$llm_worker_procs"' not in dev_script
