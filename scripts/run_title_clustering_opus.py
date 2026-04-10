"""Prepare title-only clustering batches and optionally run Claude Opus over them."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

DEFAULT_MODEL = "claude-opus-4-6"
DEFAULT_BATCH_SIZE = 2000
DEFAULT_LIMIT = 10_000
DEFAULT_OUTPUT_DIR = Path("outputs/title_clustering")

SYSTEM_PROMPT = """You are reviewing titles from a news/content feed to find duplicate or \
near-duplicate story clusters.

Cluster only when titles clearly refer to the same underlying story, launch, leak, \
announcement, incident, or repeated post.
Do not cluster merely because they mention the same company, product, or broad topic.
Be conservative. False positives are worse than missing a weak cluster.

Return strict JSON only."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create title-only clustering batches from the latest content rows and optionally "
            "run Claude Opus on each batch."
        )
    )
    parser.add_argument(
        "--input-jsonl",
        default=str(DEFAULT_OUTPUT_DIR / f"content_rows_last_{DEFAULT_LIMIT}.jsonl"),
        help="Path to the existing content-row dataset JSONL.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUTPUT_DIR / "opus_title_batches"),
        help="Directory for title-only batches and model outputs.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Anthropic model ID to use.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Titles per batch.",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Optional cap on number of batches to process.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=12000,
        help="Max tokens for the model response per batch.",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Only create title-only batches and prompts, do not call the model.",
    )
    return parser.parse_args()


@dataclass(slots=True)
class BatchFile:
    batch_id: str
    rows_path: Path
    prompt_path: Path
    response_path: Path
    parsed_path: Path
    enriched_path: Path


def _normalize_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def _title_only_row(row: dict[str, Any]) -> dict[str, Any] | None:
    display_title = (
        _normalize_text(row.get("news_item_summary_title"))
        or _normalize_text(row.get("summary_title"))
        or _normalize_text(row.get("title"))
        or _normalize_text(row.get("news_item_article_title"))
        or _normalize_text(row.get("article_title"))
    )
    if not display_title:
        return None

    return {
        "content_id": row.get("content_id"),
        "content_type": row.get("content_type"),
        "created_at": row.get("created_at"),
        "source": _normalize_text(row.get("news_item_source_label"))
        or _normalize_text(row.get("source")),
        "platform": _normalize_text(row.get("platform")),
        "domain": _normalize_text(row.get("news_item_article_domain"))
        or _normalize_text(row.get("article_domain"))
        or _normalize_text(row.get("url_domain")),
        "title": display_title,
        "title_key": _normalize_text(row.get("title_key")),
    }


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("title_key") or "",
            row.get("domain") or "",
            row.get("created_at") or "",
            row.get("content_id") or 0,
        ),
    )


def _chunk_rows(rows: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + batch_size] for index in range(0, len(rows), batch_size)]


def _build_user_prompt(*, batch_id: str, rows: list[dict[str, Any]]) -> str:
    compact_rows = [
        {
            "id": row["content_id"],
            "ts": row["created_at"],
            "src": row["source"],
            "dom": row["domain"],
            "t": row["title"],
        }
        for row in rows
    ]
    payload = json.dumps(compact_rows, ensure_ascii=False, separators=(",", ":"))
    return (
        f"Batch ID: {batch_id}\n"
        f"Titles in this batch: {len(rows)}\n\n"
        "Task:\n"
        "1. Identify exact duplicates and near-duplicate story families from title-only evidence.\n"
        "2. Create clusters only for rows that refer to the same underlying story.\n"
        "3. Leave topical neighbors unclustered.\n"
        "4. Do not emit singleton clusters. "
        "Any item not in a duplicate cluster belongs in singletons.\n\n"
        "Return JSON with this shape:\n"
        '{'
        '"batch_id":"...",'
        '"clusters":['
        '{"cluster_id":"c1","label":"short label","confidence":"high|medium|low",'
        '"member_content_ids":[1,2,3],"reason":"one short sentence"}'
        '],'
        '"singletons":[4,5,6]'
        '}\n\n'
        "Row fields:\n"
        "- id: content_id\n"
        "- ts: created_at\n"
        "- src: source label\n"
        "- dom: domain\n"
        "- t: display title\n\n"
        "Rows:\n"
        f"{payload}"
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _enrich_batch_result(
    *,
    batch_id: str,
    batch_rows: list[dict[str, Any]],
    parsed: dict[str, Any],
) -> dict[str, Any]:
    rows_by_id = {
        row.get("content_id"): {
            "content_id": row.get("content_id"),
            "content_type": row.get("content_type"),
            "created_at": row.get("created_at"),
            "source": row.get("source"),
            "platform": row.get("platform"),
            "domain": row.get("domain"),
            "title": row.get("title"),
        }
        for row in batch_rows
        if row.get("content_id") is not None
    }
    assigned_ids: set[int] = set()
    enriched_clusters: list[dict[str, Any]] = []
    for cluster in parsed.get("clusters", []):
        member_ids = [
            content_id
            for content_id in cluster.get("member_content_ids", [])
            if content_id in rows_by_id
        ]
        assigned_ids.update(member_ids)
        enriched_clusters.append(
            {
                "cluster_id": cluster.get("cluster_id"),
                "label": cluster.get("label"),
                "confidence": cluster.get("confidence"),
                "reason": cluster.get("reason"),
                "member_content_ids": member_ids,
                "members": [rows_by_id[content_id] for content_id in member_ids],
            }
        )

    singletons = [
        rows_by_id[content_id]
        for content_id in parsed.get("singletons", [])
        if content_id in rows_by_id
    ]
    if not singletons:
        singletons = [
            row
            for content_id, row in rows_by_id.items()
            if content_id not in assigned_ids
        ]

    return {
        "batch_id": batch_id,
        "cluster_count": len(enriched_clusters),
        "clustered_content_count": len(assigned_ids),
        "clusters": enriched_clusters,
        "singletons": singletons,
    }


def _prepare_batches(
    *,
    input_path: Path,
    out_dir: Path,
    batch_size: int,
    max_batches: int | None,
) -> tuple[list[BatchFile], Path]:
    rows = _load_rows(input_path)
    title_rows = [_title_only_row(row) for row in rows]
    filtered_rows = [row for row in title_rows if row is not None]
    sorted_rows = _sort_rows(filtered_rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    title_rows_path = out_dir / "title_only_rows.jsonl"
    with title_rows_path.open("w", encoding="utf-8") as handle:
        for row in sorted_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    batches: list[BatchFile] = []
    chunked_rows = _chunk_rows(sorted_rows, batch_size)
    if max_batches is not None:
        chunked_rows = chunked_rows[:max_batches]

    for index, batch_rows in enumerate(chunked_rows, start=1):
        batch_id = f"batch_{index:03d}"
        rows_path = out_dir / f"{batch_id}.rows.json"
        prompt_path = out_dir / f"{batch_id}.prompt.txt"
        response_path = out_dir / f"{batch_id}.response.txt"
        parsed_path = out_dir / f"{batch_id}.parsed.json"
        enriched_path = out_dir / f"{batch_id}.enriched.json"
        _write_json(rows_path, batch_rows)
        prompt_path.write_text(
            _build_user_prompt(batch_id=batch_id, rows=batch_rows),
            encoding="utf-8",
        )
        batches.append(
            BatchFile(
                batch_id=batch_id,
                rows_path=rows_path,
                prompt_path=prompt_path,
                response_path=response_path,
                parsed_path=parsed_path,
                enriched_path=enriched_path,
            )
        )

    manifest = {
        "input_jsonl": str(input_path),
        "title_only_rows": str(title_rows_path),
        "batch_size": batch_size,
        "batch_count": len(batches),
        "rows_total": len(sorted_rows),
        "batches": [
            {
                "batch_id": batch.batch_id,
                "rows_path": str(batch.rows_path),
                "prompt_path": str(batch.prompt_path),
                "response_path": str(batch.response_path),
                "parsed_path": str(batch.parsed_path),
                "enriched_path": str(batch.enriched_path),
            }
            for batch in batches
        ],
    }
    _write_json(out_dir / "manifest.json", manifest)
    return batches, title_rows_path


def _write_enriched_batch(batch: BatchFile, parsed: dict[str, Any]) -> None:
    batch_rows = json.loads(batch.rows_path.read_text(encoding="utf-8"))
    enriched = _enrich_batch_result(
        batch_id=batch.batch_id,
        batch_rows=batch_rows,
        parsed=parsed,
    )
    _write_json(batch.enriched_path, enriched)


def _write_run_summary(*, batches: list[BatchFile], out_dir: Path) -> None:
    enriched_batches: list[dict[str, Any]] = []
    cluster_summaries: list[dict[str, Any]] = []
    for batch in batches:
        if not batch.enriched_path.exists():
            continue
        enriched = json.loads(batch.enriched_path.read_text(encoding="utf-8"))
        enriched_batches.append(enriched)
        for cluster in enriched.get("clusters", []):
            cluster_summaries.append(
                {
                    "batch_id": enriched.get("batch_id"),
                    "cluster_id": cluster.get("cluster_id"),
                    "label": cluster.get("label"),
                    "confidence": cluster.get("confidence"),
                    "reason": cluster.get("reason"),
                    "member_count": len(cluster.get("members", [])),
                    "members": cluster.get("members", []),
                }
            )

    _write_json(out_dir / "all_batches.enriched.json", enriched_batches)
    _write_json(
        out_dir / "all_batches.cluster_summary.json",
        {
            "batch_count": len(enriched_batches),
            "cluster_count": len(cluster_summaries),
            "clusters": cluster_summaries,
        },
    )


def _normalize_parsed_result(parsed: dict[str, Any]) -> dict[str, Any]:
    raw_clusters = parsed.get("clusters", [])
    raw_singletons = parsed.get("singletons", [])
    singleton_ids: list[int] = []
    seen_singletons: set[int] = set()
    normalized_clusters: list[dict[str, Any]] = []

    for cluster in raw_clusters:
        member_ids = list(dict.fromkeys(cluster.get("member_content_ids", [])))
        if len(member_ids) < 2:
            for content_id in member_ids:
                if content_id not in seen_singletons:
                    seen_singletons.add(content_id)
                    singleton_ids.append(content_id)
            continue
        normalized_cluster = dict(cluster)
        normalized_cluster["member_content_ids"] = member_ids
        normalized_clusters.append(normalized_cluster)

    for content_id in raw_singletons:
        if content_id not in seen_singletons:
            seen_singletons.add(content_id)
            singleton_ids.append(content_id)

    return {
        "batch_id": parsed.get("batch_id"),
        "clusters": normalized_clusters,
        "singletons": singleton_ids,
    }


def _parse_response_text(text: str) -> dict[str, Any]:
    return _normalize_parsed_result(json.loads(_extract_json_text(text)))


def _extract_text_response(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []):
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    if not parts:
        raise ValueError("Anthropic response did not include text content")
    return "\n".join(parts).strip()


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _extract_first_json_object(text: str) -> str:
    start = text.find("{")
    if start == -1:
        raise ValueError("Could not locate JSON object in model response")

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise ValueError("Could not locate complete JSON object in model response")


def _extract_json_text(text: str) -> str:
    stripped = _strip_code_fence(text)
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    fence_start = stripped.find("```json")
    if fence_start != -1:
        fenced = stripped[fence_start:]
        fenced = _strip_code_fence(fenced)
        if "{" in fenced:
            return _extract_first_json_object(fenced)

    return _extract_first_json_object(stripped)


def _run_batches(
    *,
    batches: list[BatchFile],
    model: str,
    temperature: float,
    max_output_tokens: int,
) -> None:
    load_dotenv(override=False)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot run Opus batches.")

    client = Anthropic(api_key=api_key)
    for batch in batches:
        if batch.parsed_path.exists():
            parsed = json.loads(batch.parsed_path.read_text(encoding="utf-8"))
            if not batch.enriched_path.exists():
                _write_enriched_batch(batch, parsed)
            print(
                json.dumps(
                    {
                        "status": "skipping_batch",
                        "batch_id": batch.batch_id,
                        "reason": "parsed_exists",
                    }
                ),
                flush=True,
            )
            continue

        if batch.response_path.exists():
            text = batch.response_path.read_text(encoding="utf-8")
            try:
                parsed = _parse_response_text(text)
            except (json.JSONDecodeError, ValueError):
                invalid_path = batch.response_path.with_suffix(".invalid.txt")
                batch.response_path.replace(invalid_path)
                print(
                    json.dumps(
                        {
                            "status": "quarantined_invalid_response",
                            "batch_id": batch.batch_id,
                            "invalid_path": str(invalid_path),
                        }
                    ),
                    flush=True,
                )
            else:
                _write_json(batch.parsed_path, parsed)
                _write_enriched_batch(batch, parsed)
                print(
                    json.dumps(
                        {
                            "status": "parsed_existing_response",
                            "batch_id": batch.batch_id,
                            "clusters": len(parsed.get("clusters", []))
                            if isinstance(parsed, dict)
                            else None,
                        }
                    ),
                    flush=True,
                )
                continue

        print(
            json.dumps(
                {
                    "status": "starting_batch",
                    "batch_id": batch.batch_id,
                    "prompt_path": str(batch.prompt_path),
                }
            ),
            flush=True,
        )
        prompt = batch.prompt_path.read_text(encoding="utf-8")
        response = client.messages.create(
            model=model,
            max_tokens=max_output_tokens,
            temperature=temperature,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = _extract_text_response(response)
        batch.response_path.write_text(text + "\n", encoding="utf-8")
        parsed = _parse_response_text(text)
        _write_json(batch.parsed_path, parsed)
        _write_enriched_batch(batch, parsed)
        print(
            json.dumps(
                {
                    "status": "completed_batch",
                    "batch_id": batch.batch_id,
                    "clusters": len(parsed.get("clusters", []))
                    if isinstance(parsed, dict)
                    else None,
                }
            ),
            flush=True,
        )


def main() -> None:
    args = _parse_args()
    input_path = Path(args.input_jsonl)
    if not input_path.exists():
        raise FileNotFoundError(f"Input dataset not found: {input_path}")

    out_dir = Path(args.out_dir)
    batches, title_rows_path = _prepare_batches(
        input_path=input_path,
        out_dir=out_dir,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
    )

    if not args.prepare_only:
        _run_batches(
            batches=batches,
            model=args.model,
            temperature=args.temperature,
            max_output_tokens=args.max_output_tokens,
        )

    _write_run_summary(batches=batches, out_dir=out_dir)

    print(
        json.dumps(
            {
                "title_only_rows": str(title_rows_path),
                "batch_count": len(batches),
                "out_dir": str(out_dir),
                "model": args.model,
                "prepared_only": bool(args.prepare_only),
            }
        )
    )


if __name__ == "__main__":
    main()
