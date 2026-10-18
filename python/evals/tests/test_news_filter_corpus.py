"""Synthetic regression coverage for the offline public News corpus sampler."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from newsly_evals.news_filter_corpus import (
    TOPICS,
    canonical,
    load_exports,
    normalize_and_group,
    prepare_corpus,
    select_sample,
    smoke_sample,
)


def candidate(number: int, platform: str = "hackernews", topic: str = "unclassified") -> dict:
    return {
        "id": number,
        "platform": platform,
        "topic": topic,
        "article_domain": f"publisher{number}.example",
        "title": f"Story{number}",
        "summary": f"Independent factual summary numbered {number}.",
        "key_points": [{"text": f"  Evidence {number}  "}],
        "published_at": None,
        "processed_at": "2026-10-16 01:00:00",
        "ingested_at": "2026-10-16 00:00:00",
        "created_at": "2026-10-16 00:00:00",
        "public_url": f"https://publisher{number}.example/story",
        "canonical_url_hash": f"url{number}",
        "publisher_rank": 1,
        "aggregator_provenance": [{"aggregator": platform, "topic": topic}],
    }


def write_exports(path: Path, rows: list[dict], *, truncated: bool = False) -> None:
    path.mkdir()
    (path / "candidates-fixture.operator.json").write_text(
        json.dumps(
            {
                "ok": True,
                "data": {"truncated": truncated, "rows": rows},
            }
        )
    )
    (path / "export_snapshot.json").write_text(json.dumps({"public_global_only": True}))
    (path / "candidates-fixture.sql").write_text("SELECT public_factual_fields\n")


def test_topic_quotas_split_projection_and_determinism() -> None:
    rows = [candidate(number) for number in range(20)]
    for offset, topic in enumerate(TOPICS["brutalist"]):
        rows.extend(candidate(100 + offset * 10 + i, "brutalist", topic) for i in range(10))
    original = copy.deepcopy(rows)
    sample, manifest = select_sample(rows, quotas={"hackernews": 10, "brutalist": 20})
    assert manifest["sample_count"] == 30
    assert manifest["split_counts"] == {"development": 12, "test": 18}
    assert all(manifest["topic_counts"][f"brutalist:{topic}"] == 5 for topic in TOPICS["brutalist"])
    assert canonical(sample) == canonical(
        select_sample(rows, quotas={"hackernews": 10, "brutalist": 20})[0]
    )
    assert rows == original
    assert all(row["source_input"]["published_at"] is None for row in sample)
    assert all(row["source_name"] is None for row in sample)
    assert all(row["key_points"] == [f"Evidence {row['news_id']}"] for row in sample)
    assert all(
        row["summary_hash"] == hashlib.sha256(canonical(row["source_input"]).encode()).hexdigest()
        for row in sample
    )


def test_duplicate_union_retains_provenance_and_never_crosses_splits() -> None:
    rows = [candidate(number) for number in range(20)]
    rows[1]["canonical_url_hash"] = rows[0]["canonical_url_hash"]
    rows[1]["aggregator_provenance"] = [{"aggregator": "techmeme", "topic": None}]
    rows[2]["summary"] = rows[1]["summary"]
    grouped, count = normalize_and_group(rows)
    assert count == 18
    assert len({row["story_group_id"] for row in grouped[:3]}) == 1
    assert len(grouped[0]["aggregator_provenance"]) == 2
    sample, _ = select_sample(rows, quotas={"hackernews": 10})
    assert len({row["story_group_id"] for row in sample}) == 10
    smoke = smoke_sample(rows, sample, "smoke-test")
    assert len(smoke) == 4
    assert not {row["story_group_id"] for row in sample} & {row["story_group_id"] for row in smoke}
    assert all(row["split"] == "development" for row in smoke)


def test_publisher_cap_shortfall_and_repository_exception() -> None:
    rows = [candidate(number) for number in range(10)]
    for row in rows:
        row["article_domain"] = "one.example"
    with pytest.raises(ValueError, match="shortfalls"):
        select_sample(rows, quotas={"hackernews": 5}, publisher_cap=2)
    for row in rows:
        row["article_domain"] = "arxiv.org"
        row["platform"] = "hfpapers"
    sample, _ = select_sample(rows, quotas={"hfpapers": 5}, publisher_cap=2)
    assert len(sample) == 5


def test_bad_exports_and_repeated_representative_fail(tmp_path: Path) -> None:
    directory = tmp_path / "exports"
    write_exports(directory, [candidate(1)], truncated=True)
    with pytest.raises(ValueError, match="truncated"):
        load_exports(directory)
    with pytest.raises(ValueError, match="Repeated production representative"):
        normalize_and_group([candidate(1), candidate(1)])
    with pytest.raises(ValueError, match="divide evenly"):
        select_sample([], quotas={"brutalist": 5})


def test_frozen_directory_refuses_overwrite_and_records_method_hashes(tmp_path: Path) -> None:
    directory, output = tmp_path / "exports", tmp_path / "frozen"
    write_exports(directory, [candidate(number) for number in range(15)])
    manifest = prepare_corpus(directory, output, quotas={"hackernews": 5})
    assert manifest["smoke_sample_count"] == 4
    assert "candidates-fixture.sql" in manifest["method_sha256"]
    assert "news_filter_corpus.py" in manifest["method_sha256"]
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        prepare_corpus(directory, output, seed="different", quotas={"hackernews": 5})
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}
