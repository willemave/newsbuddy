"""Input integrity and time boundaries for offline lens experiments."""

import base64
import hashlib
import json
import struct
from datetime import UTC, datetime

import pytest

from newsly_evals.lens_replay import (
    checkpoints,
    embedding_input,
    normalize_rows,
    operator_rows,
)


def test_encoder_uses_canonical_title_precedence_and_whitespace() -> None:
    title, text = embedding_input(
        {
            "id": "1",
            "summary_title": " First\n headline ",
            "article_title": "Other",
            "summary_text": "  A\t summary ",
            "key_points": [" First\npoint", {"text": "Second   point"}, 42, ""],
        }
    )
    assert title == "First headline"
    assert text == "First headline\nA summary\nFirst point Second point"


def test_stale_hash_is_excluded_before_vector_use() -> None:
    records, manifest = normalize_rows(
        [
            {"id": "1", "summary_text": "Old", "input_hash": "wrong", "key_points": []},
        ]
    )
    assert records == []
    assert manifest["excluded"] == [{"id": "1", "reason": "current_input_hash_mismatch"}]


def test_normalize_validates_dimensions_and_exact_input() -> None:
    row = {
        "id": "1",
        "summary_text": "Text",
        "key_points": [],
        "available_at": 1,
        "model": "qwen/qwen3-embedding-8b",
        "encoder_version": 1,
        "dimensions": 4096,
        "platform": "hackernews",
        "input_hash": hashlib.sha256(b"Text\nText").hexdigest(),
        "vector_f32_base64": base64.b64encode(struct.pack(">4096f", *([0.5] * 4096))).decode(),
    }
    records, manifest = normalize_rows([row])
    assert len(records) == 1 and manifest["accepted_rows"] == 1
    assert records[0]["vector"][0] == 0.5
    row["dimensions"] = 4
    with pytest.raises(ValueError, match="dimensions"):
        normalize_rows([row])


def test_operator_rejects_truncated_or_duplicate_export(tmp_path) -> None:
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"ok": True, "data": {"truncated": True, "rows": []}}))
    with pytest.raises(ValueError, match="truncated"):
        operator_rows(path)
    page = json.dumps({"ok": True, "data": {"rows": [{"id": "1"}]}})
    path.write_text(page + "\n" + page)
    with pytest.raises(ValueError, match="Duplicate"):
        operator_rows(path)


def test_weekly_checkpoints_are_subset_of_nightly_and_share_endpoint() -> None:
    weekly, nightly = checkpoints("weekly"), checkpoints("nightly")
    assert set(weekly).issubset(nightly)
    assert weekly[-1] == nightly[-1]
    assert datetime.fromtimestamp(weekly[0], UTC).isoformat() == "2026-08-10T10:00:00+00:00"
