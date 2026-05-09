"""Tests for news summary schema strictness."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.metadata import GeneratedNewsSummary, NewsSummary


def test_news_summary_ignores_legacy_fields(caplog) -> None:
    """Legacy fields are ignored for news summary payloads."""
    payload = {
        "title": "Legacy Digest Title",
        "overview": "Legacy overview text.",
        "bullet_points": ["Point one", "Point two"],
    }

    summary = NewsSummary.model_validate(payload)

    assert summary.summary is None
    assert summary.key_points == []
    assert not caplog.records


def test_generated_news_summary_requires_title_and_key_points() -> None:
    """Generated news summaries should reject partial model outputs."""
    with pytest.raises(ValidationError):
        GeneratedNewsSummary.model_validate(
            {
                "classification": "skip",
                "key_points": [],
                "summary": "Short overview.",
            }
        )

    summary = GeneratedNewsSummary.model_validate(
        {
            "title": "Specific generated headline",
            "key_points": [
                "A concrete point from the source",
                "A second concrete point from the source",
            ],
            "summary": "Short overview.",
        }
    )

    assert summary.title == "Specific generated headline"
    assert summary.key_points == [
        "A concrete point from the source",
        "A second concrete point from the source",
    ]
    assert summary.summary == "Short overview."


def test_generated_news_summary_requires_non_blank_summary() -> None:
    """Generated news summaries must include the API summary field."""
    payload = {
        "title": "Specific generated headline",
        "key_points": [
            "A concrete point from the source",
            "A second concrete point from the source",
        ],
    }

    with pytest.raises(ValidationError):
        GeneratedNewsSummary.model_validate(payload)

    with pytest.raises(ValidationError):
        GeneratedNewsSummary.model_validate({**payload, "summary": "   "})

    summary = GeneratedNewsSummary.model_validate({**payload, "summary": "  Short   overview.  "})

    assert summary.summary == "Short overview."
    assert "summary" in GeneratedNewsSummary.model_json_schema()["required"]


def test_generated_news_summary_enforces_generation_limits() -> None:
    """Generated news summaries should enforce the short native-output contract."""
    with pytest.raises(ValidationError):
        GeneratedNewsSummary.model_validate(
            {
                "title": "x" * 96,
                "key_points": ["Point one", "Point two"],
                "summary": "Short overview.",
            }
        )

    with pytest.raises(ValidationError):
        GeneratedNewsSummary.model_validate(
            {
                "title": "Specific generated headline",
                "key_points": ["Only one point"],
                "summary": "Short overview.",
            }
        )

    with pytest.raises(ValidationError):
        GeneratedNewsSummary.model_validate(
            {
                "title": "Specific generated headline",
                "key_points": ["Point one", "x" * 121],
                "summary": "Short overview.",
            }
        )
