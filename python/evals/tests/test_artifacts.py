from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from newsly_evals.artifacts import read_jsonl_records, write_jsonl_artifact


class ArtifactTests(unittest.TestCase):
    def test_versioned_jsonl_round_trip_has_stable_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            digest = write_jsonl_artifact(
                path,
                [{"id": 1, "title": "One"}, {"id": 2, "title": "Two"}],
                artifact_type="newsly.test.row",
            )

            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(
                read_jsonl_records(path),
                [{"id": 1, "title": "One"}, {"id": 2, "title": "Two"}],
            )
            first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(first["schema_version"], 1)
            self.assertEqual(first["artifact_type"], "newsly.test.row")

    def test_unknown_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            path.write_text('{"schema_version":2,"id":1}\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unsupported artifact schema_version"):
                read_jsonl_records(path)


if __name__ == "__main__":
    unittest.main()
