"""Export a content-title clustering analysis dataset from a local SQLite DB."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export the latest content rows from a local SQLite DB into JSONL plus "
            "a duplicate-title helper dataset for LLM-assisted eval discovery."
        )
    )
    parser.add_argument(
        "--db-path",
        default="news_app.db",
        help="Path to the local SQLite database.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10_000,
        help="Number of latest content rows to export.",
    )
    parser.add_argument(
        "--out-dir",
        default="outputs/title_clustering",
        help="Directory for generated dataset files.",
    )
    return parser.parse_args()


def _normalize_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _normalize_title_key(*values: Any) -> str | None:
    for value in values:
        cleaned = _normalize_text(value)
        if cleaned:
            return cleaned.casefold()
    return None


def _extract_domain(url: str | None) -> str | None:
    normalized = _normalize_text(url)
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if not parsed.netloc:
        return None
    return parsed.netloc.casefold()


def _load_content_rows(db_path: Path, limit: int) -> list[sqlite3.Row]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        cursor = connection.execute(
            """
            SELECT
                c.id,
                c.content_type,
                c.url,
                c.title,
                c.source,
                c.status,
                c.classification,
                c.content_metadata,
                c.created_at,
                c.updated_at,
                c.processed_at,
                c.publication_date,
                c.platform,
                c.source_url,
                ni.id AS news_item_id,
                ni.status AS news_item_status,
                ni.summary_title AS news_item_summary_title,
                ni.article_title AS news_item_article_title,
                ni.summary_text AS news_item_summary_text,
                ni.article_url AS news_item_article_url,
                ni.article_domain AS news_item_article_domain,
                ni.discussion_url AS news_item_discussion_url,
                ni.source_label AS news_item_source_label,
                ni.source_type AS news_item_source_type,
                ni.visibility_scope AS news_item_visibility_scope,
                ni.representative_news_item_id AS news_item_representative_id,
                ni.cluster_size AS news_item_cluster_size,
                ni.ingested_at AS news_item_ingested_at
            FROM contents AS c
            LEFT JOIN news_items AS ni
                ON ni.legacy_content_id = c.id
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return cursor.fetchall()
    finally:
        connection.close()


def _build_record(row: sqlite3.Row) -> dict[str, Any]:
    metadata_raw = row["content_metadata"]
    metadata: dict[str, Any] = {}
    if isinstance(metadata_raw, str) and metadata_raw.strip():
        try:
            parsed = json.loads(metadata_raw)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            metadata = parsed

    summary = metadata.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    article = metadata.get("article")
    article = article if isinstance(article, dict) else {}

    summary_title = _normalize_text(summary.get("title"))
    summary_text = _normalize_text(summary.get("summary"))
    article_title = _normalize_text(article.get("title"))
    article_url = _normalize_text(article.get("url"))
    news_item_summary_title = _normalize_text(row["news_item_summary_title"])
    news_item_article_title = _normalize_text(row["news_item_article_title"])
    news_item_summary_text = _normalize_text(row["news_item_summary_text"])
    news_item_article_url = _normalize_text(row["news_item_article_url"])

    title = _normalize_text(row["title"])
    url = _normalize_text(row["url"])
    source_url = _normalize_text(row["source_url"])
    title_key = _normalize_title_key(
        news_item_summary_title,
        summary_title,
        title,
        news_item_article_title,
        article_title,
    )

    return {
        "content_id": row["id"],
        "content_type": row["content_type"],
        "status": row["status"],
        "classification": row["classification"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "processed_at": row["processed_at"],
        "publication_date": row["publication_date"],
        "platform": _normalize_text(row["platform"]),
        "source": _normalize_text(row["source"]),
        "url": url,
        "url_domain": _extract_domain(url),
        "source_url": source_url,
        "source_url_domain": _extract_domain(source_url),
        "title": title,
        "summary_title": summary_title,
        "article_title": article_title,
        "news_item_id": row["news_item_id"],
        "news_item_status": _normalize_text(row["news_item_status"]),
        "news_item_summary_title": news_item_summary_title,
        "news_item_article_title": news_item_article_title,
        "news_item_summary_text": news_item_summary_text,
        "news_item_article_url": news_item_article_url,
        "news_item_article_domain": _normalize_text(row["news_item_article_domain"]),
        "news_item_discussion_url": _normalize_text(row["news_item_discussion_url"]),
        "news_item_source_label": _normalize_text(row["news_item_source_label"]),
        "news_item_source_type": _normalize_text(row["news_item_source_type"]),
        "news_item_visibility_scope": _normalize_text(row["news_item_visibility_scope"]),
        "news_item_representative_id": row["news_item_representative_id"],
        "news_item_cluster_size": row["news_item_cluster_size"],
        "news_item_ingested_at": row["news_item_ingested_at"],
        "title_key": title_key,
        "summary_text": summary_text,
        "article_url": article_url,
        "article_domain": _extract_domain(article_url),
        "summary_kind": metadata.get("summary_kind"),
        "summary_version": metadata.get("summary_version"),
        "has_top_comment": isinstance(metadata.get("top_comment"), dict),
        "has_discussion_payload": isinstance(metadata.get("discussion_payload"), dict),
    }


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _build_duplicate_groups(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_title_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        title_key = record.get("title_key")
        if title_key:
            by_title_key[title_key].append(record)

    duplicate_groups = [
        {
            "title_key": title_key,
            "count": len(group),
            "display_title": next(
                (
                    item.get("summary_title") or item.get("title") or item.get("article_title")
                    for item in group
                ),
                None,
            ),
            "content_types": sorted(
                {item["content_type"] for item in group if item.get("content_type")}
            ),
            "domains": sorted(
                {
                    item.get("article_domain") or item.get("url_domain")
                    for item in group
                    if item.get("article_domain") or item.get("url_domain")
                }
            ),
            "rows": [
                {
                    "content_id": item["content_id"],
                    "content_type": item["content_type"],
                    "created_at": item["created_at"],
                    "source": item.get("source"),
                    "platform": item.get("platform"),
                    "title": item.get("title"),
                    "summary_title": item.get("summary_title"),
                    "article_title": item.get("article_title"),
                    "url": item.get("url"),
                }
                for item in group
            ],
        }
        for title_key, group in by_title_key.items()
        if len(group) > 1
    ]
    duplicate_groups.sort(key=lambda item: (-item["count"], item["display_title"] or ""))

    content_type_counts = Counter(record["content_type"] for record in records)
    return {
        "record_count": len(records),
        "duplicate_group_count": len(duplicate_groups),
        "top_duplicate_groups": duplicate_groups[:250],
        "content_type_counts": dict(sorted(content_type_counts.items())),
    }


def main() -> None:
    args = _parse_args()
    db_path = Path(args.db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _load_content_rows(db_path, args.limit)
    records = [_build_record(row) for row in rows]

    dataset_path = out_dir / f"content_rows_last_{args.limit}.jsonl"
    duplicates_path = out_dir / f"content_title_duplicates_last_{args.limit}.json"
    manifest_path = out_dir / f"content_rows_last_{args.limit}.manifest.json"

    _write_jsonl(dataset_path, records)
    duplicate_payload = _build_duplicate_groups(records)
    duplicates_path.write_text(
        json.dumps(duplicate_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "db_path": str(db_path),
                "limit": args.limit,
                "dataset_path": str(dataset_path),
                "duplicates_path": str(duplicates_path),
                "record_count": len(records),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(json.dumps({"dataset_path": str(dataset_path), "duplicates_path": str(duplicates_path)}))


if __name__ == "__main__":
    main()
