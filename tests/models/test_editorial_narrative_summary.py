from app.models.metadata import (
    ContentData,
    ContentStatus,
    ContentType,
    EditorialNarrativeSummary,
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
            "archetype_reactions": [
                {
                    "archetype": "Paul Graham",
                    "paragraphs": [
                        (
                            "Paragraph one about user pull, founder taste, and "
                            "underappreciated opportunity."
                        ),
                        (
                            "Paragraph two about what a small team could build that "
                            "incumbents miss."
                        ),
                    ],
                },
                {
                    "archetype": "Andy Grove",
                    "paragraphs": [
                        (
                            "Paragraph one about the strategic inflection point and the "
                            "main chokepoint."
                        ),
                        (
                            "Paragraph two about operating posture, competition, and what "
                            "leaders should monitor."
                        ),
                    ],
                },
                {
                    "archetype": "Charlie Munger",
                    "paragraphs": [
                        "Paragraph one about incentives, misjudgment, and hidden distortions.",
                        (
                            "Paragraph two about second-order effects, moats, and what "
                            "others misunderstand."
                        ),
                    ],
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


def test_editorial_narrative_summary_accepts_complete_archetype_set() -> None:
    summary = EditorialNarrativeSummary(
        title="Editorial Title",
        editorial_narrative=(
            "Paragraph one with concrete detail and context, including named entities, "
            "timeline anchors, and measurable outcomes that show what changed.\n\n"
            "Paragraph two with implications and evidence, describing constraints, "
            "countervailing signals, and what the source says teams should do next."
        ),
        quotes=[
            {"text": "A direct quote that should be surfaced.", "attribution": "Source Person"},
            {
                "text": "A second quote with enough detail to pass schema validation.",
                "attribution": "Industry Analyst",
            },
        ],
        archetype_reactions=[
            {
                "archetype": "Paul Graham",
                "paragraphs": [
                    "Paragraph one about demand and founder insight.",
                    "Paragraph two about leverage and startup opportunity.",
                ],
            },
            {
                "archetype": "Andy Grove",
                "paragraphs": [
                    "Paragraph one about strategic inflection and chokepoints.",
                    "Paragraph two about execution risk and leader response.",
                ],
            },
            {
                "archetype": "Charlie Munger",
                "paragraphs": [
                    "Paragraph one about incentives and distortion.",
                    "Paragraph two about second-order effects and misunderstanding.",
                ],
            },
        ],
        key_points=[
            {"point": "Point one with concrete detail."},
            {"point": "Point two with concrete detail."},
            {"point": "Point three with concrete detail."},
            {"point": "Point four with concrete detail."},
        ],
    )

    assert len(summary.archetype_reactions) == 3
    assert summary.archetype_reactions[0].archetype == "Paul Graham"
