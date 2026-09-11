from __future__ import annotations

import unittest

from newsly_evals.relations import build_feed_relation_cases, build_title_relation_cases


class FeedRelationCaseTests(unittest.TestCase):
    def test_title_cases_do_not_fabricate_content_or_provenance(self) -> None:
        cases = build_title_relation_cases(
            [{"case_id": "titles", "label": "Titles", "titles": ["One", "Two"]}]
        )

        document = cases[0]["groups"][0][0]
        self.assertIsNone(document["summary_text"])
        self.assertIsNone(document["article_domain"])
        self.assertIsNone(document["source_label"])
        self.assertIsNone(document["platform"])

    def test_frozen_rows_map_to_language_neutral_documents(self) -> None:
        cases = build_feed_relation_cases(
            [
                {
                    "case_id": "window",
                    "case_position": 2,
                    "news_item_id": 2,
                    "gold_cluster_id": "story-a",
                    "summary_title": "Acme Model 2 ships",
                    "summary_key_points": ["Available today"],
                    "canonical_story_url": "http://EXAMPLE.com/model-2#details",
                    "ingested_at": "2026-01-01T00:00:02",
                },
                {
                    "case_id": "window",
                    "case_position": 1,
                    "news_item_id": 1,
                    "gold_cluster_id": "story-a",
                    "summary_title": "Acme ships Model 2",
                    "canonical_story_url": "https://example.com/model-2",
                    "ingested_at": "2026-01-01T00:00:01Z",
                },
            ],
            label_prefix="slice",
        )

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["case_id"], "slice:window")
        self.assertEqual([item["id"] for item in cases[0]["groups"][0]], [1, 2])
        first = cases[0]["groups"][0][0]
        self.assertEqual(
            first["exact_relation_key"],
            {"kind": "story", "value": "https://example.com/model-2"},
        )
        self.assertEqual(first["ingested_at"], "2026-01-01T00:00:01Z")

    def test_missing_gold_label_becomes_a_singleton(self) -> None:
        cases = build_feed_relation_cases(
            [
                {
                    "case_id": "window",
                    "news_item_id": None,
                    "legacy_content_id": 7,
                    "summary_title": "One",
                }
            ],
            label_prefix="slice",
        )

        self.assertEqual([[7]], [[item["id"] for item in group] for group in cases[0]["groups"]])

    def test_byte_identical_reposts_share_gold_even_when_synthetic_urls_differ(self) -> None:
        cases = build_feed_relation_cases(
            [
                {
                    "case_id": "window",
                    "news_item_id": 1,
                    "summary_title": "Same complete post",
                    "gold_cluster_id": "https://example.test/one",
                },
                {
                    "case_id": "window",
                    "news_item_id": 2,
                    "summary_title": "Same complete post",
                    "gold_cluster_id": "https://example.test/two",
                },
            ],
            label_prefix="slice",
        )

        self.assertEqual([[1, 2]], [[item["id"] for item in group] for group in cases[0]["groups"]])


if __name__ == "__main__":
    unittest.main()
