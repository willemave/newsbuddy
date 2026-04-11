#!/usr/bin/env python3
"""
Add a scraper configuration for a user.

This allows users to subscribe to custom feeds (Substack, Podcasts, RSS, etc.)

Usage:
    python scripts/add_user_scraper_config.py --user-id 1 --type substack \
        --feed-url "https://www.example.com/feed" --name "My Substack"
"""

import argparse
import os
import sys
from typing import Literal, cast

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import get_db, init_db
from app.core.logging import get_logger, setup_logging
from app.models.schema import User
from app.services.scraper_configs import (
    CreateUserScraperConfig,
    create_user_scraper_config,
    list_user_scraper_configs,
)

logger = get_logger(__name__)
AllowedScraperType = Literal["substack", "atom", "podcast_rss", "youtube", "reddit"]


def list_user_configs(user_id: int) -> None:
    """List all scraper configs for a user."""
    with get_db() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            logger.error(f"User {user_id} not found")
            return

        configs = list_user_scraper_configs(db, user_id)

        logger.info(f"\nScraper configs for user {user_id} ({user.email}):")
        if not configs:
            logger.info("  No configs found")
            return

        for config in configs:
            status = "✓ active" if config.is_active else "✗ inactive"
            config_map = config.config if isinstance(config.config, dict) else {}
            logger.info(f"\n  [{config.id}] {config.display_name or 'Unnamed'} ({status})")
            logger.info(f"      Type: {config.scraper_type}")
            logger.info(f"      URL: {config.feed_url or config_map.get('feed_url', 'N/A')}")
            logger.info(f"      Config: {config.config}")


def add_config(
    user_id: int,
    scraper_type: str,
    feed_url: str,
    display_name: str | None,
    limit: int,
) -> bool:
    """Add a new scraper config for a user."""
    with get_db() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            logger.error(f"User {user_id} not found")
            return False

        try:
            config_data = CreateUserScraperConfig(
                scraper_type=cast(AllowedScraperType, scraper_type),
                display_name=display_name,
                config={
                    "feed_url": feed_url,
                    "limit": limit,
                },
                is_active=True,
            )

            new_config = create_user_scraper_config(db, user_id, config_data)

            logger.info("\n✓ Successfully added scraper config:")
            logger.info(f"  ID: {new_config.id}")
            logger.info(f"  User: {user.email}")
            logger.info(f"  Type: {new_config.scraper_type}")
            logger.info(f"  Name: {new_config.display_name}")
            logger.info(f"  URL: {new_config.feed_url}")
            logger.info(f"  Limit: {limit}")

            logger.info("\nNext steps:")
            logger.info(f"  1. Run: ./scripts/bootstrap_feeds.sh --user-only --users {user_id}")
            logger.info("  2. Run: ./scripts/start_workers.sh")

            return True

        except ValueError as e:
            logger.error(f"Failed to add config: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return False


def main():
    parser = argparse.ArgumentParser(
        description="Manage user scraper configurations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Add a Substack feed
  %(prog)s --user-id 1 --type substack \\
    --feed-url "https://importai.substack.com/feed" \\
    --name "Import AI"

  # Add a podcast feed
  %(prog)s --user-id 1 --type podcast_rss \\
    --feed-url "https://feeds.example.com/podcast.xml" \\
    --name "My Podcast" \\
    --limit 5

  # Add a generic RSS feed
  %(prog)s --user-id 1 --type atom \\
    --feed-url "https://blog.example.com/feed.xml" \\
    --name "Tech Blog"

  # List all configs for a user
  %(prog)s --user-id 1 --list
        """,
    )

    parser.add_argument(
        "--user-id",
        type=int,
        required=True,
        help="User ID to add config for",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all configs for the user",
    )
    parser.add_argument(
        "--type",
        choices=["substack", "atom", "podcast_rss", "youtube"],
        help="Scraper type (required when adding a config)",
    )
    parser.add_argument(
        "--feed-url",
        help="RSS/feed URL (required when adding a config)",
    )
    parser.add_argument(
        "--name",
        help="Display name for the feed (optional)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum items to fetch per scrape (default: 1)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(level="DEBUG" if args.debug else "INFO")

    # Initialize database
    init_db()

    # List configs
    if args.list:
        list_user_configs(args.user_id)
        return 0

    # Add config
    if not args.type or not args.feed_url:
        parser.error("--type and --feed-url are required when adding a config")

    success = add_config(
        user_id=args.user_id,
        scraper_type=args.type,
        feed_url=args.feed_url,
        display_name=args.name,
        limit=args.limit,
    )

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
