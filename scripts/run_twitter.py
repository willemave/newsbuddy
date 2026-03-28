"""Run all scheduled Twitter/X work.

This entrypoint keeps Twitter-specific scheduled work together:
- public Twitter list scraping
- per-user X bookmark/timeline/list sync fan-out

Suggested cron:
*/15 * * * * cd /opt/news_app && /opt/news_app/.venv/bin/python \
scripts/run_twitter.py >> /var/log/news_app/twitter.log 2>&1
"""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_integration_sync import enqueue_x_sync_tasks

from app.core.db import init_db
from app.core.logging import get_logger, setup_logging
from app.scraping.runner import ScraperRunner

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run scheduled Twitter/X work")
    parser.add_argument("--user-id", type=int, default=None, help="Sync one user only")
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Skip the public Twitter list scraper",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Skip the private per-user X sync enqueue",
    )
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = _parse_args()
    init_db()

    twitter_saved = 0
    twitter_scraped = 0
    sync_enqueued = 0

    if not args.skip_scrape:
        scraper_runner = ScraperRunner()
        stats = scraper_runner.run_scraper_with_stats("Twitter")
        if stats is not None:
            twitter_scraped = stats.scraped
            twitter_saved = stats.saved

    if not args.skip_sync:
        sync_enqueued = enqueue_x_sync_tasks(user_id=args.user_id)

    logger.info(
        "Twitter scheduler completed: scraped=%s saved=%s sync_tasks_enqueued=%s",
        twitter_scraped,
        twitter_saved,
        sync_enqueued,
    )


if __name__ == "__main__":
    main()
