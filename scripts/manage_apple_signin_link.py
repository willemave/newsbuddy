#!/usr/bin/env python3
"""
Detach or restore Apple Sign In for one user.

This script is designed for production maintenance workflows where you want to
temporarily remove the `apple_id` from a user record without deleting the user
or any user-owned data.

Because `users.apple_id` is non-null and unique, detach is implemented by
swapping the current value for a reserved placeholder. The original value is
written to a backup JSON file so it can be restored later.

Important:
    Detaching only `apple_id` does not allow the same Apple account to create a
    fresh login on this backend, because `users.email` is also unique. If you
    need a true "sign in as a new user again" flow, also pass
    `--also-detach-email` or update the auth flow separately.

Examples:
    Preview a detach for a user identified by email:
        python scripts/manage_apple_signin_link.py detach --email user@example.com

    Apply the detach and also move the email aside:
        python scripts/manage_apple_signin_link.py detach --email user@example.com \\
            --also-detach-email --apply

    Restore the original Apple Sign In values from a backup file:
        python scripts/manage_apple_signin_link.py restore \\
            --backup-file logs/apple_signin_detach_backups/backup.json \\
            --apply
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import get_db, init_db
from app.models.user import User

DETACHED_APPLE_ID_PREFIX = "detached.apple"
DETACHED_EMAIL_DOMAIN = "example.invalid"
DEFAULT_BACKUP_DIR = Path("logs/apple_signin_detach_backups")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Detach or restore Apple Sign In for one user",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    detach_parser = subparsers.add_parser(
        "detach",
        help="Replace a user's apple_id with a reversible placeholder",
    )
    detach_parser.add_argument(
        "--email",
        required=True,
        help="Email address linked to the user record",
    )
    detach_parser.add_argument(
        "--backup-file",
        help="Optional explicit JSON backup path",
    )
    detach_parser.add_argument(
        "--also-detach-email",
        action="store_true",
        help="Also replace the current email with a placeholder to avoid unique-email collisions",
    )
    detach_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the change. Without this flag the script only previews the mutation.",
    )

    restore_parser = subparsers.add_parser(
        "restore",
        help="Restore a previously detached apple_id from a backup JSON file",
    )
    restore_parser.add_argument(
        "--backup-file",
        required=True,
        help="Backup JSON written by the detach command",
    )
    restore_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the change. Without this flag the script only previews the mutation.",
    )

    return parser.parse_args()


def build_backup_path(
    *,
    backup_file: str | None,
    user: User,
    email_slug: str,
    timestamp_slug: str,
) -> Path:
    """Return the backup file path to use for a detach operation."""
    if backup_file:
        return Path(backup_file)
    return DEFAULT_BACKUP_DIR / f"{user.id}_{email_slug}_{timestamp_slug}.json"


def build_detached_apple_id(*, user_id: int, timestamp_slug: str) -> str:
    """Build a unique placeholder apple_id."""
    suffix = secrets.token_hex(4)
    return f"{DETACHED_APPLE_ID_PREFIX}.{user_id}.{timestamp_slug}.{suffix}"


def build_detached_email(*, user_id: int, timestamp_slug: str) -> str:
    """Build a unique placeholder email."""
    return f"detached+user{user_id}+{timestamp_slug}@{DETACHED_EMAIL_DOMAIN}"


def normalize_email_input(email: str) -> str:
    """Normalize an email string for lookup and filenames."""
    normalized_email = email.strip().lower()
    if not normalized_email:
        raise ValueError("Email is required")
    return normalized_email


def build_email_slug(email: str) -> str:
    """Build a filesystem-safe slug from an email address."""
    return email.replace("@", "_at_").replace(".", "_")


def load_user_by_email(db, email: str) -> User:
    """Load one user by normalized email."""
    normalized_email = normalize_email_input(email)
    users = db.query(User).filter(User.email == normalized_email).all()
    if not users:
        raise ValueError(f"No user found for email '{normalized_email}'")
    if len(users) > 1:
        raise ValueError(
            f"Email '{normalized_email}' matched {len(users)} users; "
            "use a unique identifier instead"
        )
    return users[0]


def build_detach_backup_record(
    *,
    user: User,
    detached_apple_id: str,
    detached_email: str | None,
    timestamp_iso: str,
) -> dict[str, Any]:
    """Build the persisted backup payload for detach/restore."""
    return {
        "version": 1,
        "operation": "apple_signin_detach",
        "detached_at": timestamp_iso,
        "user": {
            "id": user.id,
            "email": user.email,
            "apple_id": user.apple_id,
        },
        "replacement": {
            "apple_id": detached_apple_id,
            "email": detached_email,
        },
    }


def write_backup_record(path: Path, record: dict[str, Any]) -> None:
    """Write a backup record to disk."""
    if path.exists():
        raise FileExistsError(f"Backup file already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(record, indent=2, sort_keys=True)}\n", encoding="utf-8")


def read_backup_record(path: Path) -> dict[str, Any]:
    """Read and validate a backup record from disk."""
    record = json.loads(path.read_text(encoding="utf-8"))
    required_keys = {"version", "operation", "detached_at", "user", "replacement"}
    missing = required_keys - set(record)
    if missing:
        missing_keys = ", ".join(sorted(missing))
        raise ValueError(f"Backup file is missing required keys: {missing_keys}")
    if record["operation"] != "apple_signin_detach":
        raise ValueError("Backup file operation is not apple_signin_detach")
    return record


def preview_detach(
    *,
    user: User,
    detached_apple_id: str,
    detached_email: str | None,
    backup_path: Path,
) -> None:
    """Print a detach preview."""
    print("Previewing Apple Sign In detach")
    print(f"  user_id: {user.id}")
    print(f"  current_email: {user.email}")
    print(f"  current_apple_id: {user.apple_id}")
    print(f"  replacement_apple_id: {detached_apple_id}")
    print(f"  replacement_email: {detached_email or '(unchanged)'}")
    print(f"  backup_file: {backup_path}")


def preview_restore(
    *,
    user: User,
    backup_record: dict[str, Any],
) -> None:
    """Print a restore preview."""
    original_email = backup_record["user"]["email"]
    original_apple_id = backup_record["user"]["apple_id"]
    replacement_email = backup_record["replacement"].get("email")
    replacement_apple_id = backup_record["replacement"]["apple_id"]

    print("Previewing Apple Sign In restore")
    print(f"  user_id: {user.id}")
    print(f"  current_email: {user.email}")
    print(f"  current_apple_id: {user.apple_id}")
    print(f"  restore_email: {original_email if replacement_email is not None else '(unchanged)'}")
    print(f"  restore_apple_id: {original_apple_id}")
    print(f"  expected_current_apple_id: {replacement_apple_id}")


def detach_apple_signin(args: argparse.Namespace) -> int:
    """Detach Apple Sign In for one user."""
    timestamp = datetime.now(UTC)
    timestamp_slug = timestamp.strftime("%Y%m%dT%H%M%SZ")
    timestamp_iso = timestamp.isoformat().replace("+00:00", "Z")

    init_db()
    with get_db() as db:
        normalized_email = normalize_email_input(args.email)
        user = load_user_by_email(db, normalized_email)
        email_slug = build_email_slug(normalized_email)

        if user.apple_id.startswith(DETACHED_APPLE_ID_PREFIX):
            raise ValueError(
                f"User {user.id} already has a detached apple_id: {user.apple_id}"
            )

        detached_apple_id = build_detached_apple_id(
            user_id=user.id,
            timestamp_slug=timestamp_slug,
        )
        detached_email = None
        if args.also_detach_email:
            detached_email = build_detached_email(
                user_id=user.id,
                timestamp_slug=timestamp_slug,
            )

        backup_path = build_backup_path(
            backup_file=args.backup_file,
            user=user,
            email_slug=email_slug,
            timestamp_slug=timestamp_slug,
        )
        backup_record = build_detach_backup_record(
            user=user,
            detached_apple_id=detached_apple_id,
            detached_email=detached_email,
            timestamp_iso=timestamp_iso,
        )

        preview_detach(
            user=user,
            detached_apple_id=detached_apple_id,
            detached_email=detached_email,
            backup_path=backup_path,
        )

        if not args.apply:
            print("No changes applied. Re-run with --apply to commit.")
            return 0

        write_backup_record(backup_path, backup_record)

        user.apple_id = detached_apple_id
        if detached_email is not None:
            user.email = detached_email
        db.flush()

    print("Detach applied successfully.")
    print(f"Backup written to {backup_path}")
    return 0


def restore_apple_signin(args: argparse.Namespace) -> int:
    """Restore Apple Sign In for one user from a backup file."""
    backup_path = Path(args.backup_file)
    backup_record = read_backup_record(backup_path)

    user_id = int(backup_record["user"]["id"])
    original_email = str(backup_record["user"]["email"])
    original_apple_id = str(backup_record["user"]["apple_id"])
    replacement_email = backup_record["replacement"].get("email")
    replacement_apple_id = str(backup_record["replacement"]["apple_id"])

    init_db()
    with get_db() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            raise ValueError(f"User {user_id} from backup file was not found")
        if user.apple_id != replacement_apple_id:
            raise ValueError(
                "Current apple_id does not match the detached value recorded in the backup file. "
                f"current={user.apple_id} expected={replacement_apple_id}"
            )

        if replacement_email is not None and user.email != replacement_email:
            raise ValueError(
                "Current email does not match the detached value recorded in the backup file. "
                f"current={user.email} expected={replacement_email}"
            )

        preview_restore(user=user, backup_record=backup_record)

        if not args.apply:
            print("No changes applied. Re-run with --apply to commit.")
            return 0

        user.apple_id = original_apple_id
        if replacement_email is not None:
            user.email = original_email
        db.flush()

    print("Restore applied successfully.")
    return 0


def main() -> int:
    """Run the Apple Sign In maintenance script."""
    args = parse_args()

    if args.command == "detach":
        return detach_apple_signin(args)
    if args.command == "restore":
        return restore_apple_signin(args)

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
