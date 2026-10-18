"""Build a frozen public News filter corpus from bounded Rust operator exports."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

Row = dict[str, Any]
DEFAULT_SEED = "news-filter-2026-10-17"
DEFAULT_QUOTAS = {
    "brutalist": 200,
    "arxiv": 150,
    "hackernews": 100,
    "memeorandum": 130,
    "techmeme": 100,
    "hfpapers": 90,
    "finurls": 90,
    "mediagazer": 90,
    "sciurls": 50,
}
TOPICS = {
    "brutalist": ["sports", "business", "politics", "science"],
    "arxiv": ["cs.LG", "cs.CV", "cs.AI", "cs.CL", "stat.ML"],
}
STOP_WORDS = set(
    [
        "the",
        "and",
        "with",
        "from",
        "that",
        "this",
        "for",
        "into",
        "after",
        "over",
        "new",
        "says",
        "its",
        "are",
        "has",
        "how",
        "why",
        "will",
        "have",
        "their",
        "about",
        "more",
        "than",
        "what",
        "but",
        "not",
        "you",
    ]
)
MAX_CANDIDATES = 10_000


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def compact(value: Any) -> str | None:
    return " ".join(value.split()) or None if isinstance(value, str) else None


def load_exports(directory: Path) -> tuple[list[Row], dict[str, str], Row]:
    """Read only local, explicitly exported public artifacts; reject partial exports."""
    rows: list[Row] = []
    hashes = {}
    paths = sorted(directory.glob("candidates-*.operator.json"))
    if not paths:
        raise ValueError("No candidates-*.operator.json exports found")
    for path in paths:
        raw = path.read_bytes()
        payload = json.loads(raw)
        data = payload["data"]
        if payload.get("ok") is not True or data.get("truncated") is not False:
            raise ValueError(f"Failed or truncated operator export: {path.name}")
        rows.extend(data["rows"])
        hashes[path.name] = hashlib.sha256(raw).hexdigest()
        if len(rows) > MAX_CANDIDATES:
            raise ValueError(f"Candidate pool exceeds {MAX_CANDIDATES} records")
    snapshot: Row = json.loads((directory / "export_snapshot.json").read_text())
    if snapshot.get("public_global_only") is not True:
        raise ValueError("Snapshot must attest public_global_only=true")
    return rows, hashes, snapshot


def normalize_and_group(inputs: list[Row]) -> tuple[list[Row], int]:
    """Keep canonical/lexical duplicate groups in one selection and split."""
    if len(inputs) > MAX_CANDIDATES:
        raise ValueError(f"Candidate pool exceeds {MAX_CANDIDATES} records")
    rows = copy.deepcopy(inputs)
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Repeated production representative ID in candidate exports")
    parent = list(range(len(rows)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        parent[root(right)] = root(left)

    seen: dict[tuple[str, str], int] = {}
    token_index: dict[str, list[int]] = defaultdict(list)
    title_tokens: list[set[str]] = []
    for index, row in enumerate(rows):
        summary, title = compact(row["summary"]), compact(row["title"])
        if summary is None or title is None:
            raise ValueError(f"Blank factual summary/title for news {row['id']}")
        row["summary"], row["title"] = summary, title
        if title == compact(summary[:120]):
            row["title"] = summary[:120]
        if not isinstance(row["key_points"], list):
            raise ValueError(f"Invalid key_points for news {row['id']}")
        row["key_points"] = [
            value
            for point in row["key_points"]
            if (
                value := compact(
                    point
                    if isinstance(point, str)
                    else point.get("text")
                    if isinstance(point, dict)
                    else None
                )
            )
        ]
        normalized_title = " ".join(re.findall(r"\w+", row["title"].casefold()))
        for key in [
            ("url", row["canonical_url_hash"]),
            ("summary", digest(summary)),
            ("title", normalized_title),
        ]:
            if key[1]:
                if key in seen:
                    union(index, seen[key])
                else:
                    seen[key] = index
        tokens = set(re.findall(r"[a-z0-9]+", normalized_title)) - STOP_WORDS
        title_tokens.append(tokens)
        if len(tokens) >= 6:
            neighbors = {other for token in tokens for other in token_index[token]}
            for other in sorted(neighbors):
                other_tokens = title_tokens[other]
                overlap = len(tokens & other_tokens) / len(tokens | other_tokens)
                if len(other_tokens) >= 6 and overlap >= 0.8:
                    left = datetime.fromisoformat(row["ingested_at"])
                    right = datetime.fromisoformat(rows[other]["ingested_at"])
                    # Keep the frozen v1 rule; a changed grouping rule needs a new schema/seed.
                    if abs((left - right).days) <= 3:
                        union(index, other)
        for token in tokens:
            token_index[token].append(index)
    groups: dict[int, list[Row]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[root(index)].append(row)
    for members in groups.values():
        identities = "|".join(str(row["id"]) for row in sorted(members, key=lambda row: row["id"]))
        group_id = "story:" + digest(identities)[:24]
        provenance = {
            canonical(value): value for row in members for value in row["aggregator_provenance"]
        }
        for row in members:
            row["story_group_id"] = group_id
            row["aggregator_provenance"] = list(provenance.values())
    return rows, len(groups)


@dataclass
class Cell:
    quota: int
    candidates: deque[Row]
    selected: list[Row]


def select_sample(
    inputs: list[Row],
    seed: str = DEFAULT_SEED,
    quotas: dict[str, int] | None = None,
    publisher_cap: int = 50,
) -> tuple[list[Row], Row]:
    """Select requested strata or fail explicitly; never silently shrink the experiment."""
    quotas = DEFAULT_QUOTAS if quotas is None else quotas
    if not quotas or publisher_cap < 1:
        raise ValueError("Nonempty quotas and a positive publisher cap are required")
    rows, group_count = normalize_and_group(inputs)
    cells: dict[tuple[str, str], Cell] = {}
    for aggregator, quota in quotas.items():
        topics = TOPICS.get(aggregator, ["*"])
        if quota <= 0 or quota % len(topics):
            raise ValueError(f"Quota must divide evenly across {aggregator} topic cells")
        for topic in topics:
            candidates = [
                row
                for row in rows
                if row["platform"] == aggregator and (topic == "*" or row["topic"] == topic)
            ]
            candidates.sort(
                key=lambda row: (row["publisher_rank"], digest(seed + ":draw:" + str(row["id"])))
            )
            cells[(aggregator, topic)] = Cell(quota // len(topics), deque(candidates), [])
    selected_groups: set[str] = set()
    publishers: Counter[str] = Counter()
    selected: list[Row] = []
    skips: Counter[str] = Counter()
    while any(len(cell.selected) < cell.quota for cell in cells.values()):
        progress = False
        for cell in cells.values():
            if len(cell.selected) >= cell.quota:
                continue
            while cell.candidates:
                row = cell.candidates.popleft()
                if row["story_group_id"] in selected_groups:
                    skips["duplicate_story_group"] += 1
                    continue
                domain = row["article_domain"] or "unknown"
                exempt = domain == "arxiv.org" and row["platform"] in ("arxiv", "hfpapers")
                if publishers[domain] >= publisher_cap and not exempt:
                    skips["publisher_cap"] += 1
                    continue
                selected_groups.add(row["story_group_id"])
                publishers[domain] += 1
                cell.selected.append(row)
                selected.append(row)
                progress = True
                break
        if not progress:
            break
    shortfalls = {
        str(key): cell.quota - len(cell.selected)
        for key, cell in cells.items()
        if cell.quota != len(cell.selected)
    }
    if shortfalls:
        raise ValueError(f"Corpus quota shortfalls: {shortfalls}")
    for cell in cells.values():
        ordered = sorted(
            cell.selected, key=lambda row: digest(seed + ":split:" + row["story_group_id"])
        )
        for index, row in enumerate(ordered):
            row["split"] = "development" if index < len(ordered) * 2 // 5 else "test"
    output = [project(row) for row in selected]
    output.sort(key=lambda row: digest(seed + ":order:" + str(row["news_id"])))
    return output, {
        "candidate_count": len(rows),
        "candidate_story_groups": group_count,
        "sample_count": len(output),
        "aggregator_counts": dict(Counter(row["aggregator"] for row in output)),
        "topic_counts": dict(
            Counter(row["aggregator"] + ":" + row["aggregator_topic"] for row in output)
        ),
        "split_counts": dict(Counter(row["split"] for row in output)),
        "age_slice_counts": dict(Counter(row["age_slice"] for row in output)),
        "publisher_counts": dict(publishers.most_common()),
        "publisher_count": len(publishers),
        "selection_skips": dict(skips),
        "quota_shortfalls": shortfalls,
        "publisher_cap": publisher_cap,
    }


def project(row: Row) -> Row:
    source = {
        "title": row["title"],
        "summary": row["summary"],
        "key_points": row["key_points"],
        "source_name": None,
        "url": row["public_url"],
        "published_at": row["published_at"],
    }
    return {
        "news_id": row["id"],
        **{
            key: source[key]
            for key in ("title", "summary", "key_points", "source_name", "published_at")
        },
        "publisher_domain": row["article_domain"],
        "ingested_at": row["ingested_at"],
        "aggregator": row["platform"],
        "aggregator_topic": row["topic"],
        "aggregator_provenance": row["aggregator_provenance"],
        "split": row["split"],
        "age_slice": "stale_science" if row["platform"] == "sciurls" else "recent",
        "summary_hash": digest(canonical(source)),
        "story_group_id": row["story_group_id"],
        "source_input": source,
        "legacy_briefing_display_timestamp": (
            row["published_at"] or row["processed_at"] or row["ingested_at"] or row["created_at"]
        ),
        "canonical_url_hash": row["canonical_url_hash"],
    }


def prepare_corpus(
    export_dir: Path,
    output_dir: Path,
    seed: str = DEFAULT_SEED,
    quotas: dict[str, int] | None = None,
) -> Row:
    """Write a new frozen run directory; existing output directories are never modified."""
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite frozen corpus directory: {output_dir}")
    rows, hashes, snapshot = load_exports(export_dir)
    sample, manifest = select_sample(rows, seed, quotas)
    sample_text = "".join(canonical(row) + "\n" for row in sample)
    smoke = smoke_sample(rows, sample, seed)
    smoke_text = "".join(canonical(row) + "\n" for row in smoke)
    manifest.update(
        {
            "schema": "news-filter-corpus-v1",
            "seed": seed,
            "export_snapshot": snapshot,
            "source_export_sha256": hashes,
            "sample_sha256": digest(sample_text),
            "smoke_sample_sha256": digest(smoke_text),
            "smoke_sample_count": len(smoke),
            "method_sha256": method_hashes(export_dir),
            "language_audit": language_audit(sample),
            "publisher_cap_exception": {
                "domain": "arxiv.org",
                "aggregators": ["arxiv", "hfpapers"],
                "reason": "Research repository shared by two required research strata",
            },
            "deduplication": (
                "One candidate per production canonical/exact URL/compact summary/normalized title "
                "union group; title token Jaccard>=0.80 with min6tokens and v1 abs(delta.days)<=3. "
                "Semantic event variants may remain: audit before claiming event-disjoint splits."
            ),
            "source_projection": (
                "Compact factual title/summary/keypoints, source_name null; sanitized public URL; "
                "actual nullable publication date. Legacy display timestamp is separate."
            ),
            "sampling_warning": (
                "Balanced challenge set, not deployment prevalence. Publisher/topic enrichment and "
                "persona assignment prevent simple platform weighting from recovering true "
                "precision. "
                "Recent means ingestion; SciURLs is the separate stale-science challenge."
            ),
        }
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "sample.jsonl").write_text(sample_text, encoding="utf-8")
    (output_dir / "smoke-sample.jsonl").write_text(smoke_text, encoding="utf-8")
    (output_dir / "corpus-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def smoke_sample(inputs: list[Row], sample: list[Row], seed: str) -> list[Row]:
    """Pick at most four additional story groups, spread over available aggregators."""
    rows, _ = normalize_and_group(inputs)
    used = {row["story_group_id"] for row in sample}
    available = sorted(rows, key=lambda row: digest(seed + ":smoke:" + str(row["id"])))
    chosen: list[Row] = []
    aggregators: set[str] = set()
    for prefer_new_aggregator in (True, False):
        for row in available:
            if row["story_group_id"] in used:
                continue
            if prefer_new_aggregator and row["platform"] in aggregators:
                continue
            used.add(row["story_group_id"])
            aggregators.add(row["platform"])
            row["split"] = "development"
            chosen.append(project(row))
            if len(chosen) == 4:
                return chosen
    return chosen


def method_hashes(export_dir: Path) -> dict[str, str]:
    paths = sorted(export_dir.glob("candidates-*.sql"))
    paths.extend(export_dir / name for name in ("export_candidates.py", "sample_corpus.py"))
    paths.extend(
        [
            Path(__file__),
            Path(__file__).resolve().parents[2] / "scripts" / "prepare_news_filter_corpus.py",
        ]
    )
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()
    }


def language_audit(sample: list[Row]) -> Row:
    """Count non-Latin letters as an audit hint; never claim language detection."""
    counts = []
    for row in sample:
        text = " ".join([row["title"], row["summary"], *row["key_points"]])
        counts.append(
            sum(char.isalpha() and "LATIN" not in unicodedata.name(char, "") for char in text)
        )
    return {
        "method": "Unicode alphabetic characters whose names do not contain LATIN",
        "items_with_non_latin_letters": sum(count > 0 for count in counts),
        "non_latin_letter_count": sum(counts),
        "limitation": (
            "Appears predominantly English; this non-Latin audit is not a language classifier "
            "and does not establish multilingual coverage."
        ),
    }
