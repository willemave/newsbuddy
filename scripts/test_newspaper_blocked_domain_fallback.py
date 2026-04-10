"""Run newspaper4k fallback against recent failed blocked-domain news rows."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test newspaper4k fallback against the latest failed blocked-domain news rows."
    )
    parser.add_argument("--db-path", default="news_app.db", help="Path to the local SQLite DB.")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of latest failed news rows to test.",
    )
    parser.add_argument(
        "--out-path",
        default="outputs/newspaper_blocked_domain_fallback.json",
        help="Where to write the JSON results.",
    )
    return parser.parse_args()


def _load_failed_rows(
    db_path: Path,
    limit: int,
    target_domains: tuple[str, ...],
) -> list[dict[str, object]]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        placeholders = ", ".join("?" for _ in target_domains)
        query = f"""
            SELECT
                id,
                url,
                title,
                created_at,
                lower(
                    coalesce(json_extract(content_metadata, '$.article.source_domain'), source, '')
                ) AS domain
            FROM contents
            WHERE content_type = 'news'
              AND status = 'failed'
              AND lower(
                    coalesce(json_extract(content_metadata, '$.article.source_domain'), source, '')
                ) IN ({placeholders})
            ORDER BY id DESC
            LIMIT ?
        """
        rows = connection.execute(query, (*target_domains, limit)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def main() -> None:
    from app.processing_strategies.html_strategy import (
        NEWSPAPER_FALLBACK_DOMAINS,
        HtmlProcessorStrategy,
    )

    args = _parse_args()
    db_path = Path(args.db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {db_path}")

    target_domains = tuple(NEWSPAPER_FALLBACK_DOMAINS)
    rows = _load_failed_rows(db_path, args.limit, target_domains)
    strategy = HtmlProcessorStrategy(http_client=None)  # type: ignore[arg-type]

    results: list[dict[str, object]] = []
    for row in rows:
        extracted = strategy._newspaper_fallback_fetch(  # pylint: disable=protected-access
            str(row["url"]),
        )
        results.append(
            {
                "content_id": row["id"],
                "domain": row["domain"],
                "url": row["url"],
                "existing_title": row["title"],
                "created_at": row["created_at"],
                "success": extracted is not None,
                "extracted_title": extracted.get("title") if extracted else None,
                "extracted_author": extracted.get("author") if extracted else None,
                "text_length": len(str(extracted.get("text_content") or "")) if extracted else 0,
            }
        )

    payload = {
        "db_path": str(db_path),
        "limit": args.limit,
        "tested_count": len(results),
        "success_count": sum(1 for result in results if result["success"]),
        "results": results,
    }

    out_path = Path(args.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
