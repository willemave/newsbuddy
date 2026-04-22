import pytest
from pydantic import ValidationError

from app.models.contracts import ContentType
from app.models.metadata import (
    InsightReportMetadata,
    validate_content_metadata,
)


def _valid_metadata() -> dict:
    return {
        "user_id": 1,
        "subtitle": "What your library is converging on",
        "intro": "Your saves converge on one argument told from four angles.",
        "themes": ["Agent memory", "Enterprise orchestration"],
        "insights": ["Memory and orchestration are the same problem at different scales."],
        "learnings": ["Treat memory as governed state, not retrieval cache."],
        "curiosities": ["Does the 'memory benchmark race' still measure anything real?"],
        "dig_deeper_areas": [
            {
                "title": "Memory as governance",
                "prompt": (
                    "Help me compare provenance-first memory designs with "
                    "the vector-recall approaches in my library."
                ),
            }
        ],
        "referenced_knowledge_ids": [29320, 29308],
        "generated_at": "2026-04-21T04:21:27Z",
        "generated_by_model": "anthropic:claude-sonnet-4-6",
        "effort": "high",
        "image_url": "/static/images/content/insight_reports/abc.jpg",
        "thumbnail_url": "/static/images/content/insight_reports/abc_thumb.jpg",
    }


def test_insight_report_metadata_roundtrip_via_validator():
    parsed = validate_content_metadata(
        ContentType.INSIGHT_REPORT.value,
        _valid_metadata(),
    )
    assert isinstance(parsed, InsightReportMetadata)
    assert parsed.user_id == 1
    assert parsed.effort == "high"
    assert parsed.referenced_knowledge_ids == [29320, 29308]
    assert parsed.dig_deeper_areas[0].title == "Memory as governance"


def test_insight_report_metadata_requires_user_id_and_intro():
    data = _valid_metadata()
    del data["user_id"]
    with pytest.raises(ValidationError):
        InsightReportMetadata(**data)

    data = _valid_metadata()
    del data["intro"]
    with pytest.raises(ValidationError):
        InsightReportMetadata(**data)


def test_insight_report_metadata_rejects_unknown_effort():
    data = _valid_metadata()
    data["effort"] = "ultra"
    with pytest.raises(ValidationError):
        InsightReportMetadata(**data)


def test_insight_report_metadata_allows_empty_lists_and_defaults():
    minimal = {
        "user_id": 42,
        "intro": "Minimal report body for defaults.",
    }
    parsed = InsightReportMetadata(**minimal)
    assert parsed.themes == []
    assert parsed.insights == []
    assert parsed.dig_deeper_areas == []
    assert parsed.referenced_knowledge_ids == []
    assert parsed.effort is None
    assert parsed.image_url is None
