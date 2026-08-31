"""Offline title-clustering dataset and judge-prompt helpers."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from newsly_evals.artifacts import first_text, normalize_text

SYSTEM_PROMPT = (
    "You are reviewing titles from a news/content feed to find duplicate or near-duplicate "
    "story clusters.\n\n"
    "Cluster only when titles clearly refer to the same underlying story, launch, leak, "
    "announcement, incident, or repeated post.\n"
    "Do not cluster merely because they mention the same company, product, or broad topic.\n"
    "Be conservative. False positives are worse than missing a weak cluster.\n\n"
    "Return strict JSON only."
)

OUTPUT_SCHEMA = (
    '{"batch_id":"...","clusters":[{"cluster_id":"c1","label":"short label",'
    '"confidence":"high|medium|low","member_content_ids":[1,2,3],'
    '"reason":"one short sentence"}],"singletons":[4,5,6]}'
)

USER_PROMPT_TEMPLATE = (
    "Batch ID: {batch_id}\n"
    "Titles in this batch: {row_count}\n\n"
    "Task:\n"
    "1. Identify exact duplicates and near-duplicate story families from title-only evidence.\n"
    "2. Create clusters only for rows that refer to the same underlying story.\n"
    "3. Leave topical neighbors unclustered.\n"
    "4. Do not emit singleton clusters. Any item not in a duplicate cluster belongs in "
    "singletons.\n\n"
    "Return JSON with this shape:\n"
    "{output_schema}\n\n"
    "Row fields:\n"
    "- id: content_id\n"
    "- ts: created_at\n"
    "- src: source label\n"
    "- dom: domain\n"
    "- t: display title\n\n"
    "Rows:\n"
    "{payload}"
)


def render_user_prompt(*, batch_id: str, rows: Sequence[Mapping[str, Any]]) -> str:
    compact_rows = [
        {
            "id": row["content_id"],
            "ts": row.get("created_at"),
            "src": row.get("source"),
            "dom": row.get("domain"),
            "t": row["title"],
        }
        for row in rows
    ]
    return USER_PROMPT_TEMPLATE.format(
        batch_id=batch_id,
        row_count=len(rows),
        output_schema=OUTPUT_SCHEMA,
        payload=json.dumps(compact_rows, ensure_ascii=False, separators=(",", ":")),
    )


def normalize_dataset_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one Rust-exported snapshot row without accessing production state."""
    metadata = _object_value(row.get("content_metadata"))
    if not metadata:
        metadata = _object_value(row.get("raw_metadata"))
    summary = _object_value(metadata.get("summary"))
    article = _object_value(metadata.get("article"))

    summary_title = first_text(row, ("summary_title",)) or normalize_text(summary.get("title"))
    article_title = first_text(row, ("article_title",)) or normalize_text(article.get("title"))
    summary_text = first_text(row, ("summary_text",)) or normalize_text(summary.get("summary"))
    article_url = first_text(row, ("article_url",)) or normalize_text(article.get("url"))
    news_summary_title = first_text(row, ("news_item_summary_title", "summary_title"))
    news_article_title = first_text(row, ("news_item_article_title", "article_title"))
    news_summary_text = first_text(row, ("news_item_summary_text", "summary_text"))
    news_article_url = first_text(row, ("news_item_article_url", "article_url"))
    title = first_text(row, ("title",))
    url = first_text(row, ("url", "canonical_item_url"))
    source_url = first_text(row, ("source_url", "canonical_story_url"))
    title_key = _normalize_title_key(
        news_summary_title,
        summary_title,
        title,
        news_article_title,
        article_title,
    )
    content_id = row.get("content_id")
    if content_id is None:
        content_id = row.get("legacy_content_id")
    news_item_id = row.get("news_item_id")

    return {
        "content_id": content_id,
        "content_type": row.get("content_type"),
        "status": row.get("status"),
        "classification": row.get("classification"),
        "created_at": row.get("created_at", row.get("ingested_at")),
        "updated_at": row.get("updated_at"),
        "processed_at": row.get("processed_at"),
        "publication_date": row.get("publication_date", row.get("published_at")),
        "platform": first_text(row, ("platform",)),
        "source": first_text(row, ("source", "source_label")),
        "url": url,
        "url_domain": _extract_domain(url),
        "source_url": source_url,
        "source_url_domain": _extract_domain(source_url),
        "title": title,
        "summary_title": summary_title,
        "article_title": article_title,
        "news_item_id": news_item_id,
        "news_item_status": first_text(row, ("news_item_status", "status")),
        "news_item_summary_title": news_summary_title,
        "news_item_article_title": news_article_title,
        "news_item_summary_text": news_summary_text,
        "news_item_article_url": news_article_url,
        "news_item_article_domain": first_text(row, ("news_item_article_domain", "article_domain")),
        "news_item_discussion_url": first_text(row, ("news_item_discussion_url", "discussion_url")),
        "news_item_source_label": first_text(row, ("news_item_source_label", "source_label")),
        "news_item_source_type": first_text(row, ("news_item_source_type", "source_type")),
        "news_item_visibility_scope": first_text(
            row, ("news_item_visibility_scope", "visibility_scope")
        ),
        "news_item_representative_id": row.get(
            "news_item_representative_id", row.get("representative_news_item_id")
        ),
        "news_item_cluster_size": row.get("news_item_cluster_size", row.get("cluster_size")),
        "news_item_ingested_at": row.get("news_item_ingested_at", row.get("ingested_at")),
        "title_key": title_key,
        "summary_text": summary_text,
        "article_url": article_url,
        "article_domain": _extract_domain(article_url),
        "summary_kind": row.get("summary_kind", metadata.get("summary_kind")),
        "summary_version": row.get("summary_version", metadata.get("summary_version")),
        "has_top_comment": isinstance(metadata.get("top_comment"), dict),
        "has_discussion_payload": isinstance(metadata.get("discussion_payload"), dict),
    }


def title_only_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    display_title = first_text(
        row,
        (
            "news_item_summary_title",
            "summary_title",
            "title",
            "news_item_article_title",
            "article_title",
        ),
    )
    if not display_title:
        return None
    return {
        "content_id": row.get("content_id"),
        "content_type": row.get("content_type"),
        "created_at": row.get("created_at"),
        "source": first_text(row, ("news_item_source_label", "source")),
        "platform": first_text(row, ("platform",)),
        "domain": first_text(
            row,
            ("news_item_article_domain", "article_domain", "url_domain"),
        ),
        "title": display_title,
        "title_key": normalize_text(row.get("title_key")),
    }


def build_duplicate_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_title_key: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        title_key = normalize_text(record.get("title_key"))
        if title_key:
            by_title_key[title_key].append(record)
    duplicate_groups: list[dict[str, Any]] = [
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
                {str(item["content_type"]) for item in group if item.get("content_type")}
            ),
            "domains": sorted(
                {
                    domain
                    for item in group
                    for domain in [item.get("article_domain") or item.get("url_domain")]
                    if isinstance(domain, str)
                }
            ),
            "rows": [
                {
                    "content_id": item.get("content_id"),
                    "content_type": item.get("content_type"),
                    "created_at": item.get("created_at"),
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
    duplicate_groups.sort(key=lambda item: (-int(item["count"]), str(item["display_title"] or "")))
    content_type_counts = Counter(
        str(record["content_type"]) for record in records if record.get("content_type")
    )
    return {
        "record_count": len(records),
        "duplicate_group_count": len(duplicate_groups),
        "top_duplicate_groups": duplicate_groups[:250],
        "content_type_counts": dict(sorted(content_type_counts.items())),
    }


def _normalize_title_key(*values: Any) -> str | None:
    for value in values:
        cleaned = normalize_text(value)
        if cleaned:
            return cleaned.casefold()
    return None


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    return parsed.netloc.casefold() or None


def _object_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}
