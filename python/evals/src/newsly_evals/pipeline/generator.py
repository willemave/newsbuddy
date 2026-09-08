"""Fixture-only SQL compiler, guarded to disposable local databases."""

from __future__ import annotations

import json
import re

from newsly_evals.chat.generator import literal
from newsly_evals.pipeline.schema import Case


def source_url(namespace: str, source_id: str) -> str:
    return f"https://sources.example.test/{namespace}/{source_id}"


def generate_sql(case: Case, namespace: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", namespace):
        raise ValueError("invalid fixture namespace")
    lines = [
        "\\set ON_ERROR_STOP on",
        "BEGIN;",
        "SET LOCAL standard_conforming_strings = on;",
        "SET LOCAL TIME ZONE 'UTC';",
        "DO $$ BEGIN IF current_database() !~ '^newsly_eval_[a-z0-9_]+$' THEN",
        "RAISE EXCEPTION 'fixture SQL requires a newsly_eval_* database'; END IF; END $$;",
        "CREATE TEMP TABLE eval_refs (key text PRIMARY KEY, id bigint) ON COMMIT DROP;",
    ]

    def ref(key: str) -> str:
        return f"(SELECT id FROM eval_refs WHERE key={literal(key)})"

    def insert(table: str, fields: dict[str, str], key: str | None = None) -> None:
        sql = f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({', '.join(fields.values())})"
        if key:
            sql = (
                f"WITH inserted AS ({sql} RETURNING id) "
                f"INSERT INTO eval_refs SELECT {literal(key)}, id FROM inserted"
            )
        lines.append(sql + ";")

    insert(
        "users",
        {
            "apple_id": literal("eval_" + namespace),
            "email": literal(namespace + "@eval.example.test"),
            "is_admin": "FALSE",
            "is_active": "TRUE",
            "has_completed_onboarding": "TRUE",
        },
        "user:reader",
    )
    user = ref("user:reader")
    for lens in sorted({s.lens for s in case.sources if s.kind == "news"}):
        insert(
            "briefing_lenses",
            {
                "user_id": user,
                "key": literal(lens),
                "tier": literal("news"),
                "title": literal(lens.replace("_", " ").title()),
                "deck": literal("Synthetic source set"),
                "position": "0",
                "status": literal("active"),
                "centroid_weight": "0",
            },
        )
    for source in case.sources:
        url = source.url or source_url(namespace, source.id)
        key = "source:" + source.id
        if source.kind == "news":
            insert(
                "news_items",
                {
                    "ingest_key": literal(namespace + ":" + source.id),
                    "visibility_scope": literal("user"),
                    "owner_user_id": user,
                    "source_label": literal(source.source),
                    "article_url": literal(url),
                    "canonical_story_url": literal(url),
                    "summary_text": literal(source.text),
                    "summary_key_points": literal(json.dumps(source.key_points)),
                    "raw_metadata": literal(json.dumps({"article": {"title": source.title}})),
                    "status": literal("ready"),
                    "ingested_at": "CURRENT_TIMESTAMP",
                    "created_at": "CURRENT_TIMESTAMP",
                    "published_at": "CURRENT_TIMESTAMP",
                    "processed_at": "CURRENT_TIMESTAMP",
                },
                key,
            )
        else:
            metadata: dict[str, object] = {
                "transcript" if source.kind == "podcast" else "content": source.text,
                "image_generated_at": "2026-09-06T12:00:00Z",
                "image_url": "/static/images/eval.png",
            }
            if case.stage == "briefing":
                metadata["summary"] = {"title": source.title, "overview": source.text}
            insert(
                "contents",
                {
                    "content_type": literal(source.kind),
                    "url": literal(url),
                    "title": literal(source.title),
                    "source": literal(source.source),
                    "platform": literal(source.platform),
                    "publication_date": literal(source.publication_date),
                    "status": literal("processing" if case.stage == "summary" else "completed"),
                    "classification": literal("to_read"),
                    "is_aggregate": "FALSE",
                    "content_metadata": literal(json.dumps(metadata)),
                },
                key,
            )
            insert(
                "content_status",
                {"user_id": user, "content_id": ref(key), "status": literal("inbox")},
            )
        if case.stage == "summary":
            insert(
                "processing_tasks",
                {
                    "task_type": literal("summarize"),
                    "content_id": ref(key),
                    "queue_name": literal("content"),
                    "status": literal("pending"),
                    "executor_runtime": literal("rust"),
                    "executor_version": "1",
                    "executor_namespace": literal("summarize"),
                },
            )
        else:
            lens = (
                source.lens
                if source.kind == "news"
                else ("podcasts" if source.kind == "podcast" else "articles")
            )
            insert(
                "briefing_pending_sources",
                {
                    "user_id": user,
                    "lens_key": literal(lens),
                    "source_kind": literal("news" if source.kind == "news" else "content"),
                    "source_id": ref(key),
                    "enqueued_at": "CURRENT_TIMESTAMP - interval '1 hour'",
                },
            )
    lines += ["SELECT json_object_agg(key, id) FROM eval_refs;", "COMMIT;", ""]
    return "\n".join(lines)
