from app.models.contracts import SummaryKind, SummaryVersion
from app.models.domain import summary_projection


def test_legacy_interleaved_payload_projects_without_duplicate_kind_inference() -> None:
    metadata = {
        "summary": {
            "summary_type": "interleaved",
            "insights": [
                {
                    "topic": "AI",
                    "insight": "A concrete insight",
                    "supporting_quote": "A source quote",
                    "quote_attribution": "Researcher",
                },
                {"topic": "AI", "insight": "A second insight"},
            ],
        }
    }

    assert summary_projection.structured_summary(metadata) == metadata["summary"]
    assert summary_projection.bullet_points(metadata) == [
        {"text": "A concrete insight", "category": "AI"},
        {"text": "A second insight", "category": "AI"},
    ]
    assert summary_projection.quotes(metadata) == [
        {"text": "A source quote", "context": "Researcher"}
    ]
    assert summary_projection.topics(metadata) == ["AI"]


def test_explicit_interleaved_v2_uses_common_projection_fields() -> None:
    metadata = {
        "summary_kind": SummaryKind.LONG_INTERLEAVED.value,
        "summary_version": str(SummaryVersion.V2.value),
        "summary": {
            "key_points": [{"text": "A point", "category": "finding"}],
            "quotes": [{"text": "A quote", "context": "Source"}],
            "topics": [{"topic": "Systems"}],
        },
    }

    assert summary_projection.bullet_points(metadata) == [
        {"text": "A point", "category": "finding"}
    ]
    assert summary_projection.quotes(metadata) == [{"text": "A quote", "context": "Source"}]
    assert summary_projection.topics(metadata) == ["Systems"]


def test_non_display_summary_leaves_metadata_topics_to_content_adapter() -> None:
    metadata = {
        "summary_kind": SummaryKind.SHORT_NEWS.value,
        "summary_version": SummaryVersion.V1.value,
        "summary": {"summary": "Short update", "key_points": ["One"]},
        "topics": ["News"],
    }

    assert summary_projection.structured_summary(metadata) is None
    assert summary_projection.bullet_points(metadata) == []
    assert summary_projection.quotes(metadata) == []
    assert summary_projection.topics(metadata) is None
