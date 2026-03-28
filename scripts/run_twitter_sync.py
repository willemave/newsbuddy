"""Backward-compatible wrapper for the consolidated Twitter scheduler.

Suggested cron:
*/15 * * * * cd /opt/news_app && /opt/news_app/.venv/bin/python \
scripts/run_twitter.py >> /var/log/news_app/twitter.log 2>&1
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    from run_twitter import main

    main()
