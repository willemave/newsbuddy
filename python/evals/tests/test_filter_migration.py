"""Frozen reference reuse across a provider/model migration, with no model calls."""

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from newsly_evals.filtering.artifacts import (
    VARIANTS,
    digest,
    load_manifest,
    prepare,
    write_json,
    write_jsonl,
)
from newsly_evals.filtering.judge import (
    JUDGE_MODEL,
    batches,
    inspect_halts,
    prepare_judge,
    run_judge,
)
from newsly_evals.filtering.migration import (
    migrate_openrouter,
    resolve_reference_source,
    validate_equivalence,
)
from newsly_evals.filtering.transport import OPENROUTER_PROVIDER, OPENROUTER_ROUTE


def make_source(tmp_path: Path, *, partial: bool = False, audit: bool = True) -> Path:
    source = tmp_path / "source"
    rows = [
        {
            "news_id": index,
            "title": "Database research",
            "summary": "A faster database.",
            "key_points": ["Indexes improved."],
            "summary_hash": digest(index),
            "aggregator": "hackernews",
            "publisher_domain": "example.com",
            "age_slice": "recent",
            "split": "development" if index < 6 else "test",
            "story_group_id": str(index),
        }
        for index in range(12)
    ]
    profiles = [
        {
            "id": name,
            "canonical": {
                "interests": ["databases"],
                "exclusions": [],
                "match_rule": "any_interest_unless_excluded",
            },
            "variants": {variant: "databases " + variant for variant in VARIANTS},
        }
        for name in ("software_security", "science_health")
    ]
    write_jsonl(tmp_path / "sample.jsonl", rows)
    write_json(tmp_path / "profiles.json", profiles)
    manifest = prepare(tmp_path / "sample.jsonl", tmp_path / "profiles.json", source)
    prepare_judge(manifest, source)
    state: dict[str, Any] = {
        "manifest_hash": manifest["manifest_hash"],
        "max_calls": 64,
        "attempts": [],
        "results": {},
        "audit_results": {},
    }
    all_labels: list[dict[str, Any]] = []
    repeat_labels: list[dict[str, Any]] = []
    for repeated in (False, True):
        if repeated and not audit:
            continue
        planned = batches(manifest, audit=repeated)
        if partial and not repeated:
            planned = planned[:1]
        for batch in planned:
            labels = [
                {
                    "pair_id": key,
                    "label": "match",
                    "reason": "Database research.",
                    "evidence": "A faster database.",
                    "decisive_clause": "interest_1",
                }
                for key in batch["pair_ids"]
            ]
            raw = {"modelUsage": {JUDGE_MODEL: {}}, "structured_output": {"labels": labels}}
            raw_path = "judge-raw/" + batch["batch_id"] + ".json"
            write_json(source / raw_path, raw)
            state["attempts"].append(
                {
                    "batch_id": batch["batch_id"],
                    "status": "complete",
                    "audit": repeated,
                    "raw_path": raw_path,
                    "raw_hash": digest(raw),
                }
            )
            state["audit_results" if repeated else "results"][batch["batch_id"]] = {
                "labels": labels,
                "model_usage": raw["modelUsage"],
            }
            (repeat_labels if repeated else all_labels).extend(labels)
    write_json(source / "judge-checkpoint.json", state)
    write_jsonl(source / "judge-labels.jsonl", all_labels)
    if audit:
        write_jsonl(source / "judge-repeat-labels.jsonl", repeat_labels)
        write_json(
            source / "judge-repeat-audit.json",
            {
                "repeat_labels": len(repeat_labels),
                "comparable": len(repeat_labels),
                "disagreements": [],
                "disagreement_rate": 0.0,
            },
        )
    return source


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_main_migration_preserves_source_pairs_jobs_and_gold(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    before = snapshot(source)
    old = load_manifest(source / "manifest.json")
    output = tmp_path / "derived"
    migrated = migrate_openrouter(source, output, "main")
    assert snapshot(source) == before
    assert load_manifest(output / "manifest.json") == migrated
    assert migrated["schema_version"] == 2 and migrated["pairs"] == old["pairs"]
    assert migrated["transport"] == OPENROUTER_ROUTE
    assert migrated["allocation"] == {"kind": "main", "request_cap": 8384, "dollar_cap": 4.99}
    assert [job["job_id"] for job in migrated["jobs"]] == [job["job_id"] for job in old["jobs"]]
    for previous, new in zip(old["jobs"], migrated["jobs"], strict=True):
        assert previous["input_hash"] != new["input_hash"]
        assert new["request"]["provider"] == OPENROUTER_PROVIDER
        assert new["request"]["model"] == "typesafe/jev-1.14"
    root, reference, provenance = resolve_reference_source(migrated, output)
    assert root == source and reference == old
    assert provenance is not None and provenance["primary_labels"] == 24
    assert provenance["audit_labels"] == 2
    assert not (output / "judge-checkpoint.json").exists()
    assert not list(output.glob("human-review.*"))
    assert migrate_openrouter(source, output, "main") == migrated
    assert snapshot(source) == before


def test_smoke_preserves_partial_primary_without_repeat_files(tmp_path: Path) -> None:
    source = make_source(tmp_path, partial=True, audit=False)
    migrated = migrate_openrouter(source, tmp_path / "smoke", "smoke")
    assert migrated["reference_source"]["primary_labels"] == 12
    assert migrated["reference_source"]["audit_labels"] == 0
    assert "judge-repeat-labels.jsonl" not in migrated["reference_source"]["files"]
    assert migrated["allocation"]["request_cap"] == 16
    with pytest.raises(ValueError, match="complete primary and repeat"):
        migrate_openrouter(source, tmp_path / "main", "main")


@pytest.mark.parametrize("change", ["raw", "state", "extra_raw", "optional_presence"])
def test_source_descriptor_detects_changes(tmp_path: Path, change: str) -> None:
    source = make_source(tmp_path)
    output = tmp_path / "derived"
    manifest = migrate_openrouter(source, output, "main")
    if change == "raw":
        path = next((source / "judge-raw").glob("*.json"))
        path.write_text(path.read_text() + "\n")
    elif change == "state":
        path = source / "judge-checkpoint.json"
        path.write_text(path.read_text() + "\n")
    elif change == "extra_raw":
        write_json(source / "judge-raw" / "unrecorded.json", {})
    else:
        (source / "judge-repeat-audit.json").unlink()
    with pytest.raises(ValueError, match="artifact contents or exact inventory"):
        resolve_reference_source(manifest, output)


@pytest.mark.parametrize("change", ["canonical", "evidence", "variant", "job_order", "request"])
def test_equivalence_rejects_same_ids_with_different_meaning(tmp_path: Path, change: str) -> None:
    source = make_source(tmp_path)
    old = load_manifest(source / "manifest.json")
    migrated = migrate_openrouter(source, tmp_path / "derived", "main")
    altered = copy.deepcopy(migrated)
    if change == "canonical":
        altered["pairs"][0]["canonical"]["interests"] = ["sports"]
    elif change == "evidence":
        altered["pairs"][0]["evidence"]["summary"] = "Football results."
    elif change == "variant":
        altered["pairs"][0]["variants"]["prose"] = "sports"
    elif change == "job_order":
        altered["jobs"].reverse()
    else:
        altered["jobs"][0]["request"]["state"]["filter"] = "sports"
    with pytest.raises(ValueError, match="differ|changed"):
        validate_equivalence(old, altered)


def test_v2_cannot_run_or_inspect_judges(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    output = tmp_path / "derived"
    manifest = migrate_openrouter(source, output, "main")
    with pytest.raises(ValueError, match="Imported reference"):
        run_judge(manifest, output, max_calls=64)
    with pytest.raises(ValueError, match="Imported reference"):
        inspect_halts(manifest, output)
    assert not (output / "judge-checkpoint.json").exists()


def test_source_prompts_and_descriptor_are_hash_bound(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    output = tmp_path / "derived"
    manifest = migrate_openrouter(source, output, "main")
    manifest["reference_source"]["primary_labels"] = 999
    with pytest.raises(ValueError, match="descriptor hash"):
        resolve_reference_source(manifest, output)
    prompts = json.loads((source / "judge-prompts.json").read_text())
    prompts["batches"][0]["prompt"] += "Altered rubric."
    write_json(source / "judge-prompts.json", prompts)
    with pytest.raises(ValueError, match="canonical judge batches"):
        migrate_openrouter(source, tmp_path / "other", "main")


def test_migrated_scoring_reuses_original_gold_and_human_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from newsly_evals.filtering.ledger import append_event, initialize_ledger, persist_raw
    from newsly_evals.filtering.metrics import score
    from newsly_evals.filtering.provider import validate_response

    source = make_source(tmp_path)
    output = tmp_path / "derived"
    manifest = migrate_openrouter(source, output, "main")
    original = load_manifest(source / "manifest.json")
    write_jsonl(
        source / "human-labels.jsonl",
        [
            {
                "pair_id": original["pairs"][0]["pair_id"],
                "manifest_hash": original["manifest_hash"],
                "human_label": "no_match",
                "review_cohort": "random_audit",
            }
        ],
    )
    # There is one canonical human owner; a new-run worksheet is never consumed.
    (output / "human-labels.jsonl").write_text("not the reference owner's annotations\n")
    initialize_ledger(output, manifest, 4.99, 8384)
    for index, job in enumerate(manifest["jobs"]):
        usage = {"input_tokens": 100, "output_tokens": 0, "cost": 100 * 0.042 / 1_000_000}
        raw = {
            "model": OPENROUTER_ROUTE["response_model"],
            "provider": "TypeSafe",
            "id": f"fixture-{index}",
            "usage": usage,
            "answers": {
                "filter": {
                    "type": "choice",
                    "choice": "match",
                    "probabilities": {"match": 1.0, "no_match": 0.0, "insufficient_evidence": 0.0},
                }
            },
        }
        attempt = {
            "attempt_index": index,
            "job_id": job["job_id"],
            "input_hash": job["input_hash"],
            "reserved_tokens": 1000,
            "reserved_cost": 1000 * (0.042 / 1_000_000),
            "status": "reserved",
            "number": 0,
        }
        append_event(output, {"event": "reserve", "attempt_index": index, "attempt": attempt})
        raw_binding = persist_raw(output, attempt, raw)
        result = {**validate_response(raw, manifest["model"]), "input_hash": job["input_hash"]}
        append_event(
            output,
            {
                "event": "finish",
                "attempt_index": index,
                "outcome": {"status": "complete", "actual_cost": usage["cost"], **raw_binding},
                "result": result,
            },
        )
    human_path = source / "human-labels.jsonl"
    human_bytes = human_path.read_bytes()
    real_read_bytes = Path.read_bytes
    human_reads = 0

    def read_then_edit(path: Path) -> bytes:
        nonlocal human_reads
        data = real_read_bytes(path)
        if path == human_path:
            human_reads += 1
            path.write_text("edited after the scoring snapshot\n")
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_edit)
    report = score(manifest, output)
    assert human_reads == 1
    assert report["human_labels_sha256"] == hashlib.sha256(human_bytes).hexdigest()
    assert report["reference_labels"] == 24
    assert report["human_sensitivity_labels"] == 1
    assert report["reference_source"]["source_manifest_hash"] == original["manifest_hash"]
    selected = json.loads((output / "selection.json").read_text())
    assert selected["reference_source"] == report["reference_source"]
    assert report["selection"]["variant"] == "prose"
