"""Backfill canonical content body storage for existing content rows."""

from __future__ import annotations

from app.core.db import get_db
from app.models.db import Content
from app.services.content_bodies import sync_content_body_storage


def main() -> None:
    """Persist canonical content bodies and strip raw metadata for existing rows."""
    migrated = 0
    skipped = 0

    with get_db() as db:
        contents = db.query(Content).order_by(Content.id.asc()).all()
        for content in contents:
            metadata = (
                content.content_metadata if isinstance(content.content_metadata, dict) else {}
            )
            if not metadata:
                skipped += 1
                continue

            raw_candidates = (
                metadata.get("content"),
                metadata.get("transcript"),
                metadata.get("content_to_summarize"),
                (metadata.get("summary") or {}).get("full_markdown")
                if isinstance(metadata.get("summary"), dict)
                else None,
            )
            has_raw_body = any(
                isinstance(candidate, str) and candidate.strip() for candidate in raw_candidates
            )
            if not has_raw_body:
                skipped += 1
                continue

            sync_content_body_storage(db, content=content)
            migrated += 1

        db.commit()

    print(f"Backfilled content bodies for {migrated} rows; skipped {skipped} rows")


if __name__ == "__main__":
    main()
