#!/usr/bin/env python3
"""
Reset a user's onboarding state so they see onboarding again on next launch.

Usage:
    python scripts/reset_onboarding.py --user-id 1
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import get_db, init_db
from app.models.user import User


def main() -> None:
    """Reset onboarding and tutorial flags for a given user."""
    parser = argparse.ArgumentParser(description="Reset onboarding for a user")
    parser.add_argument("--user-id", type=int, required=True, help="User ID to reset")
    args = parser.parse_args()

    init_db()
    with get_db() as db:
        user = db.query(User).filter(User.id == args.user_id).first()
        if not user:
            print(f"User {args.user_id} not found.")
            sys.exit(1)

        user.has_completed_onboarding = False
        user.has_completed_new_user_tutorial = False
        db.commit()
        print(
            f"Reset onboarding for user {args.user_id} ({user.email})."
            " has_completed_onboarding=False has_completed_new_user_tutorial=False"
        )


if __name__ == "__main__":
    main()
