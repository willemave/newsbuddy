#!/usr/bin/env python
"""Report legacy `contents.news` linkage to canonical `news_items` rows."""

from __future__ import annotations

import json

from sqlalchemy import func

from app.core.db import get_db
from app.models.contracts import ContentType
from app.models.schema import Content, NewsItem


def build_report() -> dict[str, int]:
    with get_db() as db:
        legacy_total = int(
            db.query(func.count(Content.id))
            .filter(Content.content_type == ContentType.NEWS.value)
            .scalar()
            or 0
        )
        linked_legacy = int(
            db.query(func.count(NewsItem.id))
            .filter(NewsItem.legacy_content_id.is_not(None))
            .scalar()
            or 0
        )
        linked_content_ids = (
            db.query(NewsItem.legacy_content_id)
            .filter(NewsItem.legacy_content_id.is_not(None))
            .subquery()
        )
        unlinked_legacy = int(
            db.query(func.count(Content.id))
            .filter(Content.content_type == ContentType.NEWS.value)
            .filter(Content.id.not_in(linked_content_ids))
            .scalar()
            or 0
        )
        canonical_total = int(db.query(func.count(NewsItem.id)).scalar() or 0)
        canonical_without_legacy = int(
            db.query(func.count(NewsItem.id)).filter(NewsItem.legacy_content_id.is_(None)).scalar()
            or 0
        )

    return {
        "legacy_contents_news_total": legacy_total,
        "legacy_contents_news_linked_to_news_items": linked_legacy,
        "legacy_contents_news_unlinked": unlinked_legacy,
        "news_items_total": canonical_total,
        "news_items_without_legacy_content": canonical_without_legacy,
    }


def main() -> int:
    print(json.dumps(build_report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
