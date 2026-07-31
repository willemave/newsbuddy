"""Tests for deterministic summary narration text."""

from app.models.db import Content
from app.services.summary_narration import MAX_NARRATION_CHARS, build_summary_narration


def test_longform_artifact_narration_starts_with_payload_overview() -> None:
    content = Content(
        content_type="article",
        url="https://example.com/artifact",
        title="Artifact article",
        content_metadata={
            "summary_kind": "longform_artifact",
            "summary_version": 1,
            "summary": {
                "title": "Artifact article",
                "one_line": "A short feed preview that should not lead narration.",
                "ask": "learn",
                "artifact": {
                    "type": "mental_model",
                    "payload": {
                        "overview": "This overview is the summary the listener should hear first.",
                        "key_points": [
                            {
                                "heading": "First lens",
                                "content": (
                                    "Use this frame before jumping into implementation details."
                                ),
                            }
                        ],
                        "quotes": [],
                        "takeaway": "Start with the overview, then the artifact details.",
                        "extras": {
                            "mental_model": [
                                "This extra section should not become the opening narration."
                            ]
                        },
                    },
                },
            },
        },
    )

    narration = build_summary_narration(content, title="Artifact article")

    assert "This overview is the summary the listener should hear first." in narration
    assert "Point 1: First lens: Use this frame" in narration
    assert narration.index("This overview") < narration.index("Point 1")
    assert "This extra section should not become the opening narration." not in narration


def test_summary_narration_respects_total_character_limit() -> None:
    content = Content(
        content_type="article",
        url="https://example.com/long-summary",
        title="Long summary",
        content_metadata={
            "summary": {
                "overview": "N" * 5_000,
                "takeaway": "T" * 1_000,
                "key_points": [
                    {
                        "text": f"{index}:" + ("P" * 1_000),
                        "detail": f"detail {index}:" + ("D" * 1_000),
                    }
                    for index in range(10)
                ],
            }
        },
    )

    narration = build_summary_narration(content, title="A" * 1_000)

    assert len(narration) == MAX_NARRATION_CHARS
    assert narration.endswith("...")
