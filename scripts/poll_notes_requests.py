#!/usr/bin/env python3
"""Poll Apple Notes for NewsApp work and launch Codex runs."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOGS_DIR = PROJECT_ROOT / "logs" / "notes_request_poller"
DEFAULT_STATE_FILE = DEFAULT_LOGS_DIR / "state.json"
DEFAULT_LOCK_FILE = DEFAULT_LOGS_DIR / "poller.lock"
DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_SKILL_NAME = "apple-notes-request-executor"
DEFAULT_SKILL_PATH = Path.home() / ".agents" / "skills" / DEFAULT_SKILL_NAME / "SKILL.md"
DEFAULT_NOTES_HELPER = DEFAULT_SKILL_PATH.parent / "scripts" / "notes_requests.py"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Poll Apple Notes for NewsApp tasks and launch Codex workers.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Polling interval in seconds. Defaults to {DEFAULT_INTERVAL_SECONDS}.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single polling cycle and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview note selection and Codex launch without mutating Notes or spawning Codex.",
    )
    parser.add_argument(
        "--folder",
        default="NewsAppRequests",
        help="Apple Notes folder to scan.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=DEFAULT_LOGS_DIR,
        help="Directory for poller logs and state.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help="State file tracking the active Codex child.",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=DEFAULT_LOCK_FILE,
        help="Lock file used to prevent multiple poller instances.",
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex CLI binary to launch.",
    )
    parser.add_argument(
        "--codex-mode",
        choices=("yolo", "full-auto"),
        default="yolo",
        help="Codex execution mode. `yolo` maps to Codex full-access bypass mode.",
    )
    parser.add_argument(
        "--model",
        help="Optional Codex model override.",
    )
    parser.add_argument(
        "--profile",
        help="Optional Codex profile override.",
    )
    parser.add_argument(
        "--skill-name",
        default=DEFAULT_SKILL_NAME,
        help=f"Skill name to invoke. Defaults to {DEFAULT_SKILL_NAME!r}.",
    )
    parser.add_argument(
        "--skill-path",
        type=Path,
        default=DEFAULT_SKILL_PATH,
        help="Absolute path to the skill SKILL.md file.",
    )
    parser.add_argument(
        "--notes-helper",
        type=Path,
        default=DEFAULT_NOTES_HELPER,
        help="Absolute path to the Apple Notes helper script.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the poller."""
    args = parse_args()
    args.logs_dir.mkdir(parents=True, exist_ok=True)
    lock_handle = acquire_process_lock(args.lock_file)

    try:
        while True:
            run_poll_cycle(args)
            if args.once:
                return 0
            time.sleep(args.interval_seconds)
    finally:
        release_process_lock(lock_handle)


def run_poll_cycle(args: argparse.Namespace) -> None:
    """Run one poll cycle."""
    active_state = read_state_file(args.state_file)
    if active_state is not None and process_is_alive(active_state["pid"]):
        log(
            "Child still running",
            note_id=active_state["note_id"],
            pid=active_state["pid"],
        )
        return

    if active_state is not None:
        log(
            "Previous child exited",
            note_id=active_state["note_id"],
            pid=active_state["pid"],
        )
        delete_state_file(args.state_file)

    note = get_first_actionable_note(args)
    if note is None:
        log("No actionable Apple Notes requests found")
        return

    run_mode = choose_run_mode(note)
    if run_mode == "wait":
        log(
            "Waiting for 👍 approval before implementation",
            note_id=note["id"],
            title=note["name"],
        )
        return

    mark_note_in_progress(args=args, note_id=note["id"], dry_run=args.dry_run)
    if args.dry_run:
        log(
            "Dry run: would launch Codex child",
            note_id=note["id"],
            title=note["name"],
            run_mode=run_mode,
        )
        return
    launch_codex_child(args=args, note=note, run_mode=run_mode)


def choose_run_mode(note: dict[str, Any]) -> str:
    """Decide whether to plan, build, or wait based on note status."""
    if note.get("is_approved", False):
        return "implement"
    if note.get("is_in_progress", False):
        return "wait"
    return "plan"


def get_first_actionable_note(args: argparse.Namespace) -> dict[str, Any] | None:
    """Return the first actionable note, if any."""
    command = [
        "python3",
        str(args.notes_helper),
        "--folder",
        args.folder,
        "first",
    ]
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip())

    payload_text = result.stdout.strip()
    if payload_text == "null":
        return None

    payload = json.loads(payload_text)
    if not payload.get("is_actionable", False):
        return None
    return payload


def mark_note_in_progress(
    *,
    args: argparse.Namespace,
    note_id: str,
    dry_run: bool,
) -> None:
    """Mark the note with the in-progress prefix."""
    command = [
        "python3",
        str(args.notes_helper),
        "--folder",
        args.folder,
        "mark-in-progress",
        "--note-id",
        note_id,
    ]
    if dry_run:
        command.append("--dry-run")
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip())

    payload = json.loads(result.stdout)
    log(
        "Marked note in progress",
        note_id=note_id,
        title=payload["updated_title"],
        dry_run=dry_run,
    )


def launch_codex_child(
    *,
    args: argparse.Namespace,
    note: dict[str, Any],
    run_mode: str,
) -> None:
    """Launch Codex in a detached child process for the given note."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    note_slug = safe_slug(note["name"])
    run_log_path = args.logs_dir / f"{timestamp}-{note_slug}.log"
    last_message_path = args.logs_dir / f"{timestamp}-{note_slug}-last-message.txt"
    prompt = build_codex_prompt(
        folder=args.folder,
        note=note,
        run_mode=run_mode,
        skill_name=args.skill_name,
        skill_path=args.skill_path,
        notes_helper=args.notes_helper,
    )

    command = [
        args.codex_bin,
        "exec",
        "--cd",
        str(PROJECT_ROOT),
        "--add-dir",
        str(args.skill_path.parent.parent),
        "--add-dir",
        "/tmp",
        "--output-last-message",
        str(last_message_path),
    ]
    if args.codex_mode == "yolo":
        command.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        command.append("--full-auto")
    if args.model:
        command.extend(["--model", args.model])
    if args.profile:
        command.extend(["--profile", args.profile])
    command.append(prompt)

    with run_log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"[{iso_now()}] Launching: {' '.join(command[:-1])}\n")
        log_file.write(f"[{iso_now()}] Prompt: {prompt}\n\n")
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

    state = {
        "pid": process.pid,
        "note_id": note["id"],
        "title": note["name"],
        "run_mode": run_mode,
        "log_path": str(run_log_path),
        "last_message_path": str(last_message_path),
        "started_at": iso_now(),
    }
    write_state_file(args.state_file, state)
    log(
        "Launched Codex child",
        note_id=note["id"],
        pid=process.pid,
        log_path=run_log_path,
    )


def build_codex_prompt(
    *,
    folder: str,
    note: dict[str, Any],
    run_mode: str,
    skill_name: str,
    skill_path: Path,
    notes_helper: Path,
) -> str:
    """Build the prompt passed to Codex."""
    plan_requirements = (
        "Write a comprehensive plan with these sections: Problem Summary, Relevant Files "
        "and Code Paths, Implementation Steps, Verification Plan, Risks and Open Questions. "
        f"Write that plan back into the note via {notes_helper}."
    )
    common_prefix = (
        f"Use ${skill_name} at {skill_path}. "
        f"Process the Apple Notes request in folder {folder!r} with note id {note['id']!r} "
        f"and current title {note['name']!r}. "
        "Follow the skill workflow exactly. "
        "Keep the note marked with ⚙️ while working. "
        "Ask a user question only if blocked or the request is genuinely ambiguous. "
    )
    if run_mode == "plan":
        return (
            common_prefix
            + plan_requirements
            + " Do not implement the change yet. Stop after the plan is written back to the note "
            "and wait for the title to gain 👍 approval."
        )
    return (
        common_prefix
        + plan_requirements
        + " The note is already approved with 👍, so after the plan is written or refreshed, "
        "implement the change in the current repository, run appropriate verification, "
        "and mark the note done with ✅ when complete."
    )


def safe_slug(value: str) -> str:
    """Convert a string to a filesystem-safe slug."""
    return "".join(char if char.isalnum() else "-" for char in value).strip("-").lower() or "note"


def process_is_alive(pid: int) -> bool:
    """Return whether a process id is still alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_process_lock(lock_file: Path) -> Any:
    """Acquire a non-blocking lock so only one poller instance runs."""
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_file.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as err:
        handle.close()
        raise SystemExit(
            f"Another notes request poller is already running: {lock_file}"
        ) from err
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def release_process_lock(handle: Any) -> None:
    """Release the singleton poller lock."""
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def read_state_file(state_file: Path) -> dict[str, Any] | None:
    """Read the current state file if present."""
    if not state_file.exists():
        return None
    return json.loads(state_file.read_text())


def write_state_file(state_file: Path, state: dict[str, Any]) -> None:
    """Write the current child state file."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))


def delete_state_file(state_file: Path) -> None:
    """Delete the state file if present."""
    if state_file.exists():
        state_file.unlink()


def iso_now() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


def log(message: str, **context: Any) -> None:
    """Emit a timestamped log line to stdout."""
    details = " ".join(f"{key}={value}" for key, value in context.items())
    if details:
        print(f"[{iso_now()}] {message} {details}", flush=True)
        return
    print(f"[{iso_now()}] {message}", flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(signal.SIGINT + 128) from None
