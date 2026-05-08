import pytest
from pydantic import ValidationError

from app.models.metadata import (
    ContentData,
    ContentStatus,
    ContentType,
    EditorialNarrativeSummary,
    GeneratedEditorialNarrativeSummary,
)


def test_editorial_narrative_summary_maps_to_common_detail_fields():
    metadata = {
        "summary_kind": "long_editorial_narrative",
        "summary_version": 1,
        "summary": {
            "title": "Editorial Title",
            "editorial_narrative": (
                "Paragraph one with concrete detail and context, including named entities, "
                "timeline anchors, and measurable outcomes that show what changed.\n\n"
                "Paragraph two with implications and evidence, describing constraints, "
                "countervailing signals, and what the source says teams should do next."
            ),
            "quotes": [
                {
                    "text": "A direct quote that should be surfaced.",
                    "attribution": "Source Person",
                },
                {
                    "text": "A second quote with enough detail to pass schema validation.",
                    "attribution": "Industry Analyst",
                },
            ],
            "key_points": [
                {"point": "Point one with concrete detail."},
                {"point": "Point two with concrete detail."},
                {"point": "Point three with concrete detail."},
                {"point": "Point four with concrete detail."},
            ],
            "classification": "to_read",
            "summarization_date": "2026-02-08T00:00:00Z",
        },
    }

    content = ContentData(
        id=1,
        content_type=ContentType.ARTICLE,
        url="https://example.com",
        status=ContentStatus.COMPLETED,
        metadata=metadata,
    )

    assert content.structured_summary is not None
    assert len(content.bullet_points) == 4
    assert content.bullet_points[0]["text"].startswith("Point one")
    assert len(content.quotes) == 2
    assert content.quotes[0]["context"] == "Source Person"
    assert content.summary is not None
    assert content.short_summary is not None
    assert content.topics == []


def test_editorial_narrative_summary_ignores_legacy_archetype_reactions() -> None:
    summary = EditorialNarrativeSummary.model_validate(
        {
            "title": "Editorial Title",
            "editorial_narrative": (
                "Paragraph one with concrete detail and context, including named entities, "
                "timeline anchors, and measurable outcomes that show what changed.\n\n"
                "Paragraph two with implications and evidence, describing constraints, "
                "countervailing signals, and what the source says teams should do next."
            ),
            "quotes": [
                {"text": "A direct quote that should be surfaced.", "attribution": "Source Person"},
                {
                    "text": "A second quote with enough detail to pass schema validation.",
                    "attribution": "Industry Analyst",
                },
            ],
            "archetype_reactions": [
                {
                    "archetype": "Paul Graham",
                    "paragraphs": [
                        "Paragraph one about demand and founder insight.",
                        "Paragraph two about leverage and startup opportunity.",
                    ],
                }
            ],
            "key_points": [
                {"point": "Point one with concrete detail."},
                {"point": "Point two with concrete detail."},
                {"point": "Point three with concrete detail."},
                {"point": "Point four with concrete detail."},
            ],
        }
    )

    assert len(summary.key_points) == 4
    assert "archetype_reactions" not in summary.model_dump(mode="json")


def test_editorial_narrative_summary_accepts_research_source_details() -> None:
    summary = EditorialNarrativeSummary.model_validate(
        {
            "title": "Research Summary",
            "editorial_narrative": (
                "Paragraph one explains the paper's core question, setup, and key result with "
                "enough concrete detail to satisfy the narrative summary validation rules.\n\n"
                "Paragraph two covers the evidence, caveats, and why the result matters for "
                "builders or researchers evaluating the work."
            ),
            "quotes": [
                {
                    "text": "A direct quote from the paper with enough detail.",
                    "attribution": "Paper",
                },
                {
                    "text": "A second quote describing the result or limitation in detail.",
                    "attribution": "Authors",
                },
            ],
            "key_points": [
                {"point": "Point one with concrete research detail."},
                {"point": "Point two with concrete research detail."},
                {"point": "Point three with concrete research detail."},
                {"point": "Point four with concrete research detail."},
            ],
            "source_details": {
                "template": "research",
                "hypothesis": "Scaling retrieval on curated corpora improves answer quality.",
                "methods": ["Benchmarking against three retrieval baselines."],
                "arguments": [
                    "The tuned retrieval path materially improved answer relevance.",
                    "Most gains came from corpus curation rather than model size.",
                ],
                "limitations": ["The evaluation set is narrow and enterprise-focused."],
                "implications": ["Teams should measure data quality before changing models."],
            },
        }
    )

    assert summary.source_details is not None
    assert summary.source_details.template == "research"


def test_generated_editorial_narrative_summary_enforces_generation_limits() -> None:
    valid_payload = {
        "title": "Editorial Title",
        "editorial_narrative": (
            "Paragraph one with concrete detail and context, including named entities, "
            "timeline anchors, and measurable outcomes that show what changed. "
            "Paragraph two covers implications and evidence, describing constraints, "
            "countervailing signals, and what the source says teams should do next."
        ),
        "quotes": [
            {"text": "A direct quote that should be surfaced.", "attribution": "Source Person"},
            {
                "text": "A second quote with enough detail to pass schema validation.",
                "attribution": "Industry Analyst",
            },
        ],
        "key_points": [
            {"point": "Point one with concrete detail."},
            {"point": "Point two with concrete detail."},
            {"point": "Point three with concrete detail."},
            {"point": "Point four with concrete detail."},
        ],
    }

    summary = GeneratedEditorialNarrativeSummary.model_validate(valid_payload)

    assert len(summary.quotes) == 2
    assert len(summary.key_points) == 4


def test_generated_editorial_narrative_summary_rejects_long_outputs() -> None:
    valid_payload = {
        "title": "Editorial Title",
        "editorial_narrative": (
            "Paragraph one with concrete detail and context, including named entities, "
            "timeline anchors, and measurable outcomes that show what changed. "
            "Paragraph two covers implications and evidence, describing constraints, "
            "countervailing signals, and what the source says teams should do next."
        ),
        "quotes": [
            {"text": "A direct quote that should be surfaced.", "attribution": "Source Person"},
            {
                "text": "A second quote with enough detail to pass schema validation.",
                "attribution": "Industry Analyst",
            },
            {"text": "A third quote should exceed generation limits.", "attribution": "Extra"},
        ],
        "key_points": [
            {"point": "Point one with concrete detail."},
            {"point": "Point two with concrete detail."},
            {"point": "Point three with concrete detail."},
            {"point": "Point four with concrete detail."},
        ],
    }

    with pytest.raises(ValidationError):
        GeneratedEditorialNarrativeSummary.model_validate(valid_payload)
