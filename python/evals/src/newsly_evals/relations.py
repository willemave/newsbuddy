"""Python model pipelines around the production Rust relation policy."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import numpy as np
from numpy.typing import NDArray

PROTOCOL_VERSION = 1


class EmbeddingEncoder(Protocol):
    """Minimal interface implemented by local and hosted eval model pipelines."""

    model: str

    def encode(self, texts: Sequence[str]) -> NDArray[np.floating[Any]]:
        """Return one finite vector per input text."""


class RustEvalDriver:
    """Strict JSON subprocess client for the production Rust eval boundary."""

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        timeout_seconds: float = 300.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._command = tuple(command or _default_driver_command())
        if not self._command:
            raise ValueError("driver command must not be empty")
        self._timeout_seconds = timeout_seconds

    def prepare_relations(self, cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return self._invoke(
            "prepare-relations",
            {"version": PROTOCOL_VERSION, "cases": list(cases)},
        )

    def score_relations(
        self,
        *,
        cases: Sequence[Mapping[str, Any]],
        embedding_bundle: Mapping[str, Any],
        thresholds: Sequence[Mapping[str, Any]],
        include_traces: bool = False,
    ) -> dict[str, Any]:
        return self._invoke(
            "score-relations",
            {
                "version": PROTOCOL_VERSION,
                "cases": list(cases),
                "embedding_bundle": dict(embedding_bundle),
                "thresholds": list(thresholds),
                "include_traces": include_traces,
            },
        )

    def _invoke(self, subcommand: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        completed = subprocess.run(
            [*self._command, subcommand],
            input=json.dumps(payload, separators=(",", ":")),
            capture_output=True,
            check=False,
            encoding="utf-8",
            timeout=self._timeout_seconds,
        )
        if completed.returncode != 0:
            diagnostic = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(
                f"Rust eval driver failed with exit {completed.returncode}: {diagnostic[:2000]}"
            )
        decoded = json.loads(completed.stdout)
        if not isinstance(decoded, dict) or decoded.get("version") != PROTOCOL_VERSION:
            raise ValueError("Rust eval driver returned an invalid protocol envelope")
        return decoded


def build_title_relation_cases(raw_cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert curated title families into language-neutral Rust policy inputs."""
    cases: list[dict[str, Any]] = []
    for raw_case in raw_cases:
        case_id = _required_string(raw_case, "case_id")
        label = _required_string(raw_case, "label")
        raw_groups = raw_case.get("groups")
        if raw_groups is None:
            raw_groups = [raw_case.get("titles")]
        if not isinstance(raw_groups, list) or not raw_groups:
            raise ValueError(f"relation case {case_id!r} has no groups")
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        next_id = 1
        groups: list[list[dict[str, Any]]] = []
        for raw_group in raw_groups:
            if not isinstance(raw_group, list) or not raw_group:
                raise ValueError(f"relation case {case_id!r} has an empty group")
            group: list[dict[str, Any]] = []
            for raw_title in raw_group:
                if not isinstance(raw_title, str) or not raw_title.strip():
                    raise ValueError(f"relation case {case_id!r} has an invalid title")
                title = " ".join(raw_title.split())
                group.append(
                    {
                        "id": next_id,
                        "primary_title": title,
                        "related_titles": [],
                        "summary_key_points": [],
                        "summary_text": title,
                        "article_domain": f"source{next_id}.example.com",
                        "source_label": f"Source {next_id}",
                        "platform": "eval",
                        "exact_relation_key": None,
                        "ingested_at": (base_time + timedelta(seconds=next_id))
                        .isoformat()
                        .replace("+00:00", "Z"),
                    }
                )
                next_id += 1
            groups.append(group)
        cases.append({"case_id": case_id, "label": label, "groups": groups})
    return cases


def build_embedding_bundle(
    *,
    prepared: Mapping[str, Any],
    encoder: EmbeddingEncoder,
    provider_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Encode Rust-canonical texts and validate a reproducible bundle."""
    raw_items = prepared.get("texts")
    if not isinstance(raw_items, list):
        raise ValueError("prepare response is missing texts")
    texts = [_required_string(item, "text") for item in raw_items]
    started_at = perf_counter()
    vectors = np.asarray(encoder.encode(texts), dtype=np.float64)
    elapsed_ms = (perf_counter() - started_at) * 1000
    if vectors.ndim != 2 or vectors.shape[0] != len(texts) or vectors.shape[1] == 0:
        raise ValueError(
            "embedding response shape mismatch: "
            f"expected ({len(texts)}, dimensions), got {vectors.shape}"
        )
    if not np.isfinite(vectors).all():
        raise ValueError("embedding response contains a non-finite component")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms <= np.finfo(np.float64).eps):
        raise ValueError("embedding response contains a zero-norm vector")
    normalized = vectors / norms
    items = []
    for raw_item, vector in zip(raw_items, normalized, strict=True):
        items.append(
            {
                "id": _required_string(raw_item, "id"),
                "text_sha256": _required_string(raw_item, "text_sha256"),
                "vector": vector.tolist(),
            }
        )
    return {
        "version": PROTOCOL_VERSION,
        "model": encoder.model,
        "dimensions": int(normalized.shape[1]),
        "normalization": "l2",
        "items": items,
        "timings_ms": {"encoding": elapsed_ms, "total": elapsed_ms},
        "provider_metadata": dict(provider_metadata or {}),
    }


def run_relation_eval(
    *,
    raw_cases: Sequence[Mapping[str, Any]],
    encoder: EmbeddingEncoder,
    thresholds: Sequence[Mapping[str, Any]],
    driver: RustEvalDriver | None = None,
    include_traces: bool = False,
    provider_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one Python-built embedding pipeline through Rust production policy."""
    cases = build_title_relation_cases(raw_cases)
    return run_document_relation_eval(
        cases=cases,
        encoder=encoder,
        thresholds=thresholds,
        driver=driver,
        include_traces=include_traces,
        provider_metadata=provider_metadata,
    )


def run_document_relation_eval(
    *,
    cases: Sequence[Mapping[str, Any]],
    encoder: EmbeddingEncoder,
    thresholds: Sequence[Mapping[str, Any]],
    driver: RustEvalDriver | None = None,
    include_traces: bool = False,
    provider_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Embed language-neutral documents and delegate every relation decision to Rust."""
    rust = driver or RustEvalDriver()
    prepared = rust.prepare_relations(cases)
    bundle = build_embedding_bundle(
        prepared=prepared,
        encoder=encoder,
        provider_metadata=provider_metadata,
    )
    result = rust.score_relations(
        cases=cases,
        embedding_bundle=bundle,
        thresholds=thresholds,
        include_traces=include_traces,
    )
    result["embedding_bundle_metadata"] = {
        key: value for key, value in bundle.items() if key != "items"
    }
    return result


def build_feed_relation_cases(
    records: Sequence[Mapping[str, Any]],
    *,
    label_prefix: str,
) -> list[dict[str, Any]]:
    """Adapt a frozen feed JSONL snapshot to the Rust relation protocol.

    This function only maps artifact fields. Canonical matching text, semantic
    prefiltering, scoring, guards, cluster bridging, and aggregation remain
    exclusively owned by the Rust eval driver.
    """
    records_by_case: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        case_id = _optional_string(record.get("case_id")) or "unknown"
        records_by_case[case_id].append(record)

    cases: list[dict[str, Any]] = []
    for case_id, case_records in records_by_case.items():
        ordered_records = sorted(case_records, key=_feed_record_order)
        groups_by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
        group_order: list[str] = []
        for record in ordered_records:
            document = _feed_relation_document(record)
            gold_label = _optional_string(record.get("gold_cluster_id"))
            if not gold_label:
                gold_label = f"singleton:{document['id']}"
            if gold_label not in groups_by_label:
                group_order.append(gold_label)
            groups_by_label[gold_label].append(document)
        cases.append(
            {
                "case_id": f"{label_prefix}:{case_id}",
                "label": f"{label_prefix} feed window {case_id}",
                "groups": [groups_by_label[label] for label in group_order],
            }
        )
    return cases


def _feed_relation_document(record: Mapping[str, Any]) -> dict[str, Any]:
    item_id = record.get("news_item_id")
    if item_id is None:
        item_id = record.get("legacy_content_id")
    if not isinstance(item_id, int) or isinstance(item_id, bool):
        raise ValueError("feed eval record requires an integer news_item_id or legacy_content_id")
    summary_key_points = record.get("summary_key_points")
    if not isinstance(summary_key_points, list):
        summary_key_points = []
    return {
        "id": item_id,
        "primary_title": _optional_string(record.get("summary_title"))
        or _optional_string(record.get("article_title")),
        "related_titles": [],
        "summary_key_points": [
            cleaned
            for value in summary_key_points
            if (cleaned := _optional_string(value)) is not None
        ],
        "summary_text": _optional_string(record.get("summary_text")),
        "article_domain": _optional_string(record.get("article_domain")),
        "source_label": _optional_string(record.get("source_label")),
        "platform": _optional_string(record.get("platform")),
        "exact_relation_key": _feed_exact_relation_key(record),
        "ingested_at": _utc_timestamp(record.get("ingested_at")),
    }


def _feed_exact_relation_key(record: Mapping[str, Any]) -> dict[str, str] | None:
    for kind, keys in (
        ("story", ("canonical_story_url", "article_url")),
        ("item", ("canonical_item_url", "discussion_url")),
    ):
        for key in keys:
            normalized = _normalize_http_url(record.get(key))
            if normalized:
                return {"kind": kind, "value": normalized}
    platform = _optional_string(record.get("platform"))
    external_id = _optional_string(record.get("source_external_id"))
    if platform and external_id:
        return {"kind": "external", "value": f"{platform}:{external_id}"}
    return None


def _normalize_http_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    hostname = parsed.hostname.casefold()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if port is not None and port != 443:
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    return urlunsplit(("https", netloc, path, parsed.query, ""))


def _utc_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid feed eval timestamp {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _feed_record_order(record: Mapping[str, Any]) -> tuple[str, int, int]:
    timestamp = _utc_timestamp(record.get("ingested_at")) or ""
    position = record.get("case_position")
    item_id = record.get("news_item_id")
    if item_id is None:
        item_id = record.get("legacy_content_id")
    return (
        timestamp,
        position if isinstance(position, int) and not isinstance(position, bool) else 0,
        item_id if isinstance(item_id, int) and not isinstance(item_id, bool) else 0,
    )


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _default_driver_command() -> list[str]:
    override = os.environ.get("NEWSLY_EVAL_DRIVER")
    if override:
        command = shlex.split(override)
        if not command:
            raise ValueError("NEWSLY_EVAL_DRIVER must not be blank")
        return command
    repository_root = Path(__file__).resolve().parents[4]
    binary = repository_root / "rust" / "target" / "debug" / "newsly-eval-driver"
    if binary.is_file() and os.access(binary, os.X_OK):
        return [str(binary)]
    return [
        "cargo",
        "run",
        "--quiet",
        "--manifest-path",
        str(repository_root / "rust" / "Cargo.toml"),
        "-p",
        "newsly-eval-driver",
        "--",
    ]


def _required_string(value: Any, key: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"expected an object containing {key!r}")
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError(f"field {key!r} must be a nonempty string")
    return candidate
