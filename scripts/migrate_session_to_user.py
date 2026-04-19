#!/usr/bin/env python3
"""
Migrate favorites and read status from session_id to user_id in the live database.

This script migrates ALL existing session-based data to a specified user_id.
Run this BEFORE the Alembic migration that deletes session data.

Usage:
    # List available users
    python scripts/migrate_session_to_user.py --list-users

    # Show how much data would be migrated
    python scripts/migrate_session_to_user.py --user-id 5 --dry-run

    # Migrate all data to a user
    python scripts/migrate_session_to_user.py --user-id 5
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import inspect, select, text

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import contextlib

from app.core.db import get_db
from app.models.user import User


def check_schema(db) -> dict[str, bool]:
    """Check which schema version is in use."""
    inspector = inspect(db.bind)

    result = {
        "has_favorites": "content_favorites" in inspector.get_table_names(),
        "has_read_status": "content_read_status" in inspector.get_table_names(),
        "has_unlikes": "content_unlikes" in inspector.get_table_names(),
        "uses_session_id": False,
        "uses_user_id": False,
    }

    # Check content_favorites schema
    if result["has_favorites"]:
        columns = [col["name"] for col in inspector.get_columns("content_favorites")]
        if "session_id" in columns:
            result["uses_session_id"] = True
        elif "user_id" in columns:
            result["uses_user_id"] = True

    return result


def get_session_data_counts(db) -> dict[str, int]:
    """Get counts of session-based data."""
    counts = {
        "favorites": 0,
        "read_status": 0,
        "unlikes": 0,
    }

    with contextlib.suppress(Exception):
        counts["favorites"] = db.execute(text("SELECT COUNT(*) FROM content_favorites")).scalar()

    with contextlib.suppress(Exception):
        counts["read_status"] = db.execute(
            text("SELECT COUNT(*) FROM content_read_status")
        ).scalar()

    with contextlib.suppress(Exception):
        counts["unlikes"] = db.execute(text("SELECT COUNT(*) FROM content_unlikes")).scalar()

    return counts


def migrate_to_user(db, user_id: int, dry_run: bool = False) -> dict[str, int]:
    """
    Migrate all session-based data to user_id-based schema.

    Args:
        db: Database session
        user_id: User ID to migrate data to
        dry_run: If True, only show what would be migrated

    Returns:
        Dictionary with migration statistics
    """
    stats = {
        "favorites_found": 0,
        "favorites_migrated": 0,
        "favorites_skipped": 0,
        "read_status_found": 0,
        "read_status_migrated": 0,
        "read_status_skipped": 0,
        "unlikes_found": 0,
        "unlikes_migrated": 0,
        "unlikes_skipped": 0,
    }

    # Migrate favorites
    print("\n🔍 Loading all favorites from database...")
    result = db.execute(
        text("SELECT session_id, content_id, favorited_at, created_at FROM content_favorites")
    )
    old_favorites = result.fetchall()

    stats["favorites_found"] = len(old_favorites)
    print(f"   Found {stats['favorites_found']} favorite records")

    if not dry_run and stats["favorites_found"] > 0:
        # Get existing user favorites to avoid duplicates
        existing = db.execute(
            text("SELECT content_id FROM content_favorites WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).fetchall()
        existing_content_ids = {row[0] for row in existing}

        # Clear old session-based data
        print("   Clearing old session-based favorites...")
        db.execute(
            text("DELETE FROM content_favorites WHERE session_id IS NOT NULL OR user_id IS NULL")
        )

        # Insert new user-based records
        for _session_id, content_id, favorited_at, created_at in old_favorites:
            if content_id in existing_content_ids:
                stats["favorites_skipped"] += 1
                continue

            db.execute(
                text("""
                    INSERT INTO content_favorites (user_id, content_id, favorited_at, created_at)
                    VALUES (:user_id, :content_id, :favorited_at, :created_at)
                """),
                {
                    "user_id": user_id,
                    "content_id": content_id,
                    "favorited_at": favorited_at,
                    "created_at": created_at,
                },
            )
            stats["favorites_migrated"] += 1

    # Migrate read status
    print("\n🔍 Loading all read status from database...")
    result = db.execute(
        text("SELECT session_id, content_id, read_at, created_at FROM content_read_status")
    )
    old_read_status = result.fetchall()

    stats["read_status_found"] = len(old_read_status)
    print(f"   Found {stats['read_status_found']} read status records")

    if not dry_run and stats["read_status_found"] > 0:
        # Get existing user read status to avoid duplicates
        existing = db.execute(
            text("SELECT content_id FROM content_read_status WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).fetchall()
        existing_content_ids = {row[0] for row in existing}

        # Clear old session-based data
        print("   Clearing old session-based read status...")
        db.execute(
            text("DELETE FROM content_read_status WHERE session_id IS NOT NULL OR user_id IS NULL")
        )

        # Insert new user-based records
        for _session_id, content_id, read_at, created_at in old_read_status:
            if content_id in existing_content_ids:
                stats["read_status_skipped"] += 1
                continue

            db.execute(
                text("""
                    INSERT INTO content_read_status (user_id, content_id, read_at, created_at)
                    VALUES (:user_id, :content_id, :read_at, :created_at)
                """),
                {
                    "user_id": user_id,
                    "content_id": content_id,
                    "read_at": read_at,
                    "created_at": created_at,
                },
            )
            stats["read_status_migrated"] += 1

    # Migrate unlikes
    print("\n🔍 Loading all unlikes from database...")
    result = db.execute(
        text("SELECT session_id, content_id, unliked_at, created_at FROM content_unlikes")
    )
    old_unlikes = result.fetchall()

    stats["unlikes_found"] = len(old_unlikes)
    print(f"   Found {stats['unlikes_found']} unlike records")

    if not dry_run and stats["unlikes_found"] > 0:
        # Get existing user unlikes to avoid duplicates
        existing = db.execute(
            text("SELECT content_id FROM content_unlikes WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).fetchall()
        existing_content_ids = {row[0] for row in existing}

        # Clear old session-based data
        print("   Clearing old session-based unlikes...")
        db.execute(
            text("DELETE FROM content_unlikes WHERE session_id IS NOT NULL OR user_id IS NULL")
        )

        # Insert new user-based records
        for _session_id, content_id, unliked_at, created_at in old_unlikes:
            if content_id in existing_content_ids:
                stats["unlikes_skipped"] += 1
                continue

            db.execute(
                text("""
                    INSERT INTO content_unlikes (user_id, content_id, unliked_at, created_at)
                    VALUES (:user_id, :content_id, :unliked_at, :created_at)
                """),
                {
                    "user_id": user_id,
                    "content_id": content_id,
                    "unliked_at": unliked_at,
                    "created_at": created_at,
                },
            )
            stats["unlikes_migrated"] += 1

    if not dry_run:
        db.commit()
        print("\n✅ Migration committed to database")
    else:
        print("\n⚠️  Dry run - no changes made")

    return stats


def list_users(db) -> list[User]:
    """List all users in the database."""
    users = db.execute(select(User)).scalars().all()
    return list(users)


def main():
    parser = argparse.ArgumentParser(
        description="Migrate ALL session-based data to user-based tracking in live database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List available users
  python scripts/migrate_session_to_user.py --list-users

  # Show what would be migrated (dry run)
  python scripts/migrate_session_to_user.py --user-id 5 --dry-run

  # Migrate all data to user
  python scripts/migrate_session_to_user.py --user-id 5

Note: This should be run BEFORE the Alembic migration that deletes session data.
      Run with --dry-run first to see what will be migrated.
        """,
    )
    parser.add_argument("--list-users", action="store_true", help="List all users in database")
    parser.add_argument("--user-id", type=int, help="User ID to migrate all data to")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be migrated without making changes"
    )

    args = parser.parse_args()

    # List users
    if args.list_users:
        print("\n📋 Available users:\n")
        with get_db() as db:
            users = list_users(db)
            if not users:
                print("   No users found in database")
                return

            for user in users:
                admin_badge = "👑 ADMIN" if user.is_admin else ""
                active_badge = "✅ ACTIVE" if user.is_active else "❌ INACTIVE"
                user_name = user.full_name or "(no name)"
                print(
                    f"   ID: {user.id:3d} | {user.email:40s} | {user_name:30s} | "
                    f"{admin_badge:8s} | {active_badge}"
                )
        return

    # Validate migration arguments
    if args.user_id is None:
        print("❌ Error: --user-id is required")
        print("   Use --list-users to see available users")
        sys.exit(1)

    # Check database schema
    print("\n" + "=" * 80)
    print("🔍 CHECKING DATABASE SCHEMA")
    print("=" * 80)

    with get_db() as db:
        schema = check_schema(db)

        if schema["uses_user_id"]:
            print("✅ Database is already using user_id schema")
            print("\n⚠️  WARNING: This script is for migrating FROM session_id TO user_id")
            print("   Your database already uses user_id, so there's nothing to migrate.")
            print("\n   If you want to copy data between users, you'll need a different script.")
            sys.exit(0)

        if not schema["uses_session_id"]:
            print("❌ Database doesn't have session_id columns")
            print("   Cannot determine schema version. Check your database.")
            sys.exit(1)

        print("✅ Database is using session_id schema (ready to migrate)")

        # Get data counts
        counts = get_session_data_counts(db)
        print("\n📊 Current data in database:")
        print(f"   Favorites:    {counts['favorites']:6d} records")
        print(f"   Read Status:  {counts['read_status']:6d} records")
        print(f"   Unlikes:      {counts['unlikes']:6d} records")
        total = sum(counts.values())
        print(f"   {'─' * 30}")
        print(f"   Total:        {total:6d} records")

        if total == 0:
            print("\n⚠️  No data to migrate!")
            return

    # Run migration
    print("\n" + "=" * 80)
    print("🔄 MIGRATION CONFIGURATION")
    print("=" * 80)
    print(f"Target user ID:   {args.user_id}")
    mode_label = "DRY RUN (no changes)" if args.dry_run else "LIVE (will modify database)"
    print(f"Mode:             {mode_label}")
    print("Strategy:         Migrate ALL session data to user_id")
    print("=" * 80)

    with get_db() as db:
        # Verify user exists
        user = db.execute(select(User).where(User.id == args.user_id)).scalar_one_or_none()
        if not user:
            print(f"\n❌ Error: User ID {args.user_id} not found in database")
            print("   Use --list-users to see available users")
            sys.exit(1)

        print(f"\n✅ Target user verified: {user.email} ({user.full_name or 'no name'})")

        # Run migration
        stats = migrate_to_user(
            db=db,
            user_id=args.user_id,
            dry_run=args.dry_run,
        )

        # Print summary
        print("\n" + "=" * 80)
        print("📊 MIGRATION SUMMARY")
        print("=" * 80)
        print(
            f"Favorites:    {stats['favorites_found']:4d} found | "
            f"{stats['favorites_migrated']:4d} migrated | "
            f"{stats['favorites_skipped']:4d} skipped"
        )
        print(
            f"Read Status:  {stats['read_status_found']:4d} found | "
            f"{stats['read_status_migrated']:4d} migrated | "
            f"{stats['read_status_skipped']:4d} skipped"
        )
        print(
            f"Unlikes:      {stats['unlikes_found']:4d} found | "
            f"{stats['unlikes_migrated']:4d} migrated | "
            f"{stats['unlikes_skipped']:4d} skipped"
        )
        print("=" * 80)

        total_migrated = (
            stats["favorites_migrated"] + stats["read_status_migrated"] + stats["unlikes_migrated"]
        )
        total_skipped = (
            stats["favorites_skipped"] + stats["read_status_skipped"] + stats["unlikes_skipped"]
        )

        if args.dry_run:
            print(
                f"\n💡 DRY RUN: Would migrate {total_migrated} records "
                f"(skip {total_skipped} duplicates)"
            )
            print("   Run without --dry-run to apply changes")
        else:
            print(
                f"\n✅ COMPLETE: Migrated {total_migrated} records "
                f"(skipped {total_skipped} duplicates)"
            )
            print("\n⚠️  NEXT STEP: Run the Alembic migration to update table schema:")
            print("   python -m alembic -c migrations/alembic.ini upgrade head")


if __name__ == "__main__":
    main()
