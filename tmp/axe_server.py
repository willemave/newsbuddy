"""Standalone seeded API server for AXe-driven iOS validation.

Spins up an isolated temporary Postgres schema (no dev-DB pollution), loads the
shared showcase scenario, then serves the FastAPI app in development mode so
the iOS app's debug login endpoint can issue a session for that user.

Usage:  uv run python tmp/axe_server.py [PORT]
Prints  AXE_USER_ID=<id>  and  AXE_PORT=<port>  for the caller to consume.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure the repo root is importable regardless of invocation directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Must be set before importing the app so debug auth (/auth/debug/new-user) is enabled.
os.environ["ENVIRONMENT"] = "development"
os.environ["DEBUG"] = "true"
AXE_IMAGES_DIR = Path(tempfile.mkdtemp(prefix="newsly-axe-images-"))
os.environ["IMAGES_BASE_DIR"] = str(AXE_IMAGES_DIR)

import uvicorn  # noqa: E402

import app.core.db as core_db  # noqa: E402
from app.testing.postgres_harness import create_temporary_postgres_harness  # noqa: E402
from scripts.support.dev_user import setup_showcase_user  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099


def seed(db) -> int:
    result = setup_showcase_user(db, briefing_mode="deterministic")
    user_id = int(result["user"]["id"])
    content = result["content"]
    print(
        "  seeded showcase user "
        f"{user_id}: {content['articles']} articles, {content['podcasts']} podcasts, "
        f"{content['news']} news items"
    )
    return user_id


def main() -> None:
    harness = create_temporary_postgres_harness(schema_prefix="newsly_axe")
    try:
        core_db._engine = harness.engine
        core_db._SessionLocal = harness.session_factory

        db = harness.session_factory()
        try:
            user_id = seed(db)
        finally:
            db.close()

        print(f"AXE_USER_ID={user_id}")
        print(f"AXE_PORT={PORT}")
        sys.stdout.flush()

        # Import after globals are bound so the served app uses the harness schema.
        from app.main import app

        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
    finally:
        harness.close()
        shutil.rmtree(AXE_IMAGES_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
