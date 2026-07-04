#!/usr/bin/env python3
"""Generate Newsly auth tokens for a user id."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.security import create_access_token, create_refresh_token
from app.core.settings import get_settings


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate access and refresh tokens for a Newsly user id.",
    )
    parser.add_argument(
        "-u",
        "--user-id",
        required=True,
        type=positive_int,
        help="User id to encode in the generated tokens.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print tokens as JSON instead of shell-style lines.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    access_token = create_access_token(args.user_id)
    refresh_token = create_refresh_token(args.user_id)

    if args.json:
        print(
            json.dumps(
                {
                    "user_id": args.user_id,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "access_token_expires_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
                    "refresh_token_expires_days": settings.REFRESH_TOKEN_EXPIRE_DAYS,
                },
                indent=2,
            )
        )
        return

    print(f"USER_ID={args.user_id}")
    print(f"ACCESS_TOKEN={access_token}")
    print(f"REFRESH_TOKEN={refresh_token}")
    print(f"ACCESS_TOKEN_EXPIRES_MINUTES={settings.ACCESS_TOKEN_EXPIRE_MINUTES}")
    print(f"REFRESH_TOKEN_EXPIRES_DAYS={settings.REFRESH_TOKEN_EXPIRE_DAYS}")


if __name__ == "__main__":
    main()
