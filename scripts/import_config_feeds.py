#!/usr/bin/env python
"""Import feeds from YAML config files into UserScraperConfig table.

This script reads feeds from config/*.yml files and creates UserScraperConfig
records for specified users.
"""

import argparse
import logging
import sys
from pathlib import Path

import yaml

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def load_yaml_config(config_path: Path) -> list[dict]:
    """Load feeds from a YAML config file."""
    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}")
        return []

    with open(config_path) as f:
        data = yaml.safe_load(f)
        return data.get("feeds", [])


def import_feeds_for_user(user_id: int, clear_existing: bool = False) -> dict[str, int]:
    """Import all feeds from config files for a user.

    Args:
        user_id: User ID to import feeds for
        clear_existing: If True, delete existing configs first

    Returns:
        Dict with counts by scraper type
    """
    from app.core.db import get_db
    from app.models.schema import UserScraperConfig
    from app.services.scraper_configs import CreateUserScraperConfig, create_user_scraper_config

    config_dir = project_root / "config"
    stats = {
        "substack": 0,
        "podcast_rss": 0,
        "atom": 0,
        "skipped": 0,
        "errors": 0,
    }

    with get_db() as db:
        # Clear existing configs if requested
        if clear_existing:
            deleted = db.query(UserScraperConfig).filter_by(user_id=user_id).delete()
            db.commit()
            logger.info(f"Deleted {deleted} existing configs for user {user_id}")

        # Import Substack feeds
        logger.info("Importing Substack feeds...")
        substack_feeds = load_yaml_config(config_dir / "substack.yml")
        for feed in substack_feeds:
            try:
                # Check if already exists (simple check - get all and filter in Python)
                if not clear_existing:
                    existing_configs = (
                        db.query(UserScraperConfig)
                        .filter_by(user_id=user_id, scraper_type="substack")
                        .all()
                    )
                    exists = any(
                        isinstance(cfg.config, dict) and cfg.config.get("feed_url") == feed["url"]
                        for cfg in existing_configs
                    )
                    if exists:
                        logger.debug(f"Skipping existing: {feed['name']}")
                        stats["skipped"] += 1
                        continue

                config_data = CreateUserScraperConfig(
                    scraper_type="substack",
                    display_name=feed.get("name"),
                    config={
                        "feed_url": feed["url"],
                        "limit": feed.get("limit", 10),
                    },
                    is_active=True,
                )
                create_user_scraper_config(db, user_id, config_data)
                logger.info(f"  ✓ {feed['name']}")
                stats["substack"] += 1
            except Exception as e:
                logger.error(f"  ✗ Failed to import {feed.get('name', feed['url'])}: {e}")
                stats["errors"] += 1

        # Import Podcast feeds
        logger.info("Importing Podcast feeds...")
        podcast_feeds = load_yaml_config(config_dir / "podcasts.yml")
        for feed in podcast_feeds:
            try:
                # Check if already exists (simple check - get all and filter in Python)
                if not clear_existing:
                    existing_configs = (
                        db.query(UserScraperConfig)
                        .filter_by(user_id=user_id, scraper_type="podcast_rss")
                        .all()
                    )
                    exists = any(
                        isinstance(cfg.config, dict) and cfg.config.get("feed_url") == feed["url"]
                        for cfg in existing_configs
                    )
                    if exists:
                        logger.debug(f"Skipping existing: {feed['name']}")
                        stats["skipped"] += 1
                        continue

                config_data = CreateUserScraperConfig(
                    scraper_type="podcast_rss",
                    display_name=feed.get("name"),
                    config={
                        "feed_url": feed["url"],
                        "limit": feed.get("limit", 10),
                    },
                    is_active=True,
                )
                create_user_scraper_config(db, user_id, config_data)
                logger.info(f"  ✓ {feed['name']}")
                stats["podcast_rss"] += 1
            except Exception as e:
                logger.error(f"  ✗ Failed to import {feed.get('name', feed['url'])}: {e}")
                stats["errors"] += 1

        # Import Atom feeds (if not example)
        atom_file = config_dir / "atom.yml"
        if atom_file.exists():
            logger.info("Importing Atom feeds...")
            atom_feeds = load_yaml_config(atom_file)
            for feed in atom_feeds:
                # Skip example feeds
                if "example.com" in feed["url"]:
                    continue

                try:
                    # Check if already exists (simple check - get all and filter in Python)
                    if not clear_existing:
                        existing_configs = (
                            db.query(UserScraperConfig)
                            .filter_by(user_id=user_id, scraper_type="atom")
                            .all()
                        )
                        exists = any(
                            isinstance(cfg.config, dict)
                            and cfg.config.get("feed_url") == feed["url"]
                            for cfg in existing_configs
                        )
                        if exists:
                            logger.debug(f"Skipping existing: {feed['name']}")
                            stats["skipped"] += 1
                            continue

                    config_data = CreateUserScraperConfig(
                        scraper_type="atom",
                        display_name=feed.get("name"),
                        config={
                            "feed_url": feed["url"],
                            "limit": feed.get("limit", 10),
                        },
                        is_active=True,
                    )
                    create_user_scraper_config(db, user_id, config_data)
                    logger.info(f"  ✓ {feed['name']}")
                    stats["atom"] += 1
                except Exception as e:
                    logger.error(f"  ✗ Failed to import {feed.get('name', feed['url'])}: {e}")
                    stats["errors"] += 1

    return stats


def main():
    from app.core.db import get_db, init_db

    parser = argparse.ArgumentParser(
        description="Import feeds from config files into UserScraperConfig"
    )
    parser.add_argument(
        "--user-id",
        type=int,
        help="User ID to import feeds for (default: all users)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing configs before importing",
    )
    args = parser.parse_args()

    init_db()

    # Get user IDs to process
    with get_db() as db:
        from app.models.user import User

        if args.user_id:
            user_ids = [args.user_id]
        else:
            users = db.query(User).filter_by(is_active=True).all()
            user_ids = [user.id for user in users]

    if not user_ids:
        logger.error("No users found")
        return 1

    logger.info(f"Importing feeds for {len(user_ids)} user(s)")

    # Import for each user
    total_stats = {"substack": 0, "podcast_rss": 0, "atom": 0, "skipped": 0, "errors": 0}
    for user_id in user_ids:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Processing User ID: {user_id}")
        logger.info(f"{'=' * 60}")
        stats = import_feeds_for_user(user_id, clear_existing=args.clear)

        for key in total_stats:
            total_stats[key] += stats[key]

    # Print summary
    logger.info(f"\n{'=' * 60}")
    logger.info("Import Summary")
    logger.info(f"{'=' * 60}")
    logger.info(f"Substack feeds:  {total_stats['substack']}")
    logger.info(f"Podcast feeds:   {total_stats['podcast_rss']}")
    logger.info(f"Atom feeds:      {total_stats['atom']}")
    logger.info(f"Skipped:         {total_stats['skipped']}")
    logger.info(f"Errors:          {total_stats['errors']}")
    total_imported = total_stats["substack"] + total_stats["podcast_rss"] + total_stats["atom"]
    logger.info(f"Total imported:  {total_imported}")

    return 0 if total_stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
