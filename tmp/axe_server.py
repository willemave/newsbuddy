"""Standalone seeded API server for AXe-driven iOS validation.

Spins up an isolated temporary Postgres schema (no dev-DB pollution), seeds a
user with varied content (articles, podcasts, news, read history, knowledge
saves), then serves the FastAPI app in development mode so the iOS app's E2E
auto-login (`/auth/debug/new-user` with a user id) works.

Usage:  uv run python tmp/axe_server.py [PORT]
Prints  AXE_USER_ID=<id>  and  AXE_PORT=<port>  for the caller to consume.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Ensure the repo root is importable regardless of invocation directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Must be set before importing the app so debug auth (/auth/debug/new-user) is enabled.
os.environ["ENVIRONMENT"] = "development"
os.environ["DEBUG"] = "true"

import uvicorn  # noqa: E402

import app.core.db as core_db  # noqa: E402
from app.testing.postgres_harness import create_temporary_postgres_harness  # noqa: E402
from tests.support.builders import (  # noqa: E402
    create_content_knowledge_save_row,
    create_content_read_status_row,
    create_content_row,
    create_content_status_entry_row,
    create_news_item_row,
    create_user_row,
)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099


def seed(db) -> int:
    user = create_user_row(
        db,
        index=1,
        apple_id="axe_apple_id",
        email="axe@example.com",
        full_name="AXe Tester",
        has_completed_onboarding=True,
        has_completed_new_user_tutorial=True,
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    idx = 0

    def add_content(content_type: str, title: str, source: str, *, read: bool, saved: bool) -> None:
        nonlocal idx
        idx += 1
        content = create_content_row(
            db,
            index=idx,
            content_type=content_type,
            title=title,
            url=f"https://example.com/axe/{content_type}/{idx}",
            source=source,
            status="completed",
            publication_date=now - timedelta(hours=idx),
        )
        create_content_status_entry_row(db, user=user, content=content, status="inbox")
        if read:
            create_content_read_status_row(
                db, user=user, content=content, read_at=now - timedelta(minutes=idx)
            )
        if saved:
            create_content_knowledge_save_row(db, user=user, content=content)

    # Articles — mix of unread, read (recently-read), and knowledge-saved.
    add_content(
        "article",
        "The Quiet Revolution in Local Search",
        "techreview.example",
        read=False,
        saved=False,
    )
    add_content(
        "article",
        "How Memory Caching Reshaped Mobile UX",
        "ui.example",
        read=False,
        saved=True,
    )
    add_content(
        "article",
        "A Field Guide to Concurrency Bugs",
        "eng.example",
        read=True,
        saved=False,
    )
    add_content(
        "article",
        "Designing for One Accent Color",
        "design.example",
        read=True,
        saved=True,
    )
    add_content(
        "article",
        "The Economics of Small Software",
        "biz.example",
        read=False,
        saved=False,
    )
    add_content("article", "Notes on Editorial Typography", "type.example", read=True, saved=False)

    # Podcasts — several so detail-swipe navigation has a sequence to traverse.
    add_content(
        "podcast",
        "Episode 41: The Reader's Palette",
        "pod.example",
        read=False,
        saved=False,
    )
    add_content("podcast", "Episode 42: Marking Things Read", "pod.example", read=True, saved=False)
    add_content("podcast", "Episode 43: The Skip Button", "pod.example", read=False, saved=False)
    add_content("podcast", "Episode 44: Cascade Navigation", "pod.example", read=True, saved=False)

    # News items (Fast tab) — best effort; don't fail the seed if the model shifts.
    news_count = 0
    for n in range(1, 6):
        try:
            create_news_item_row(
                db,
                index=n,
                article_title=f"Breaking: Test Story {n}",
                summary_title=f"Breaking: Test Story {n}",
            )
            news_count += 1
        except Exception as exc:  # pragma: no cover - defensive seed
            print(f"  (news item {n} skipped: {exc})")

    db.commit()
    db.refresh(user)
    if user.id is None:
        raise RuntimeError("Seeded AXe user did not receive an id")
    print(f"  seeded {idx} content items + {news_count} news items for user {user.id}")
    return user.id


def main() -> None:
    harness = create_temporary_postgres_harness(schema_prefix="newsly_axe")
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

    try:
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
    finally:
        harness.close()


if __name__ == "__main__":
    main()
