"""Tests for news summary schema strictness."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.metadata import GeneratedNewsSummary, NewsSummary


def test_news_summary_ignores_legacy_fields(caplog) -> None:
    """Legacy fields are ignored for news summary payloads."""
    payload = {
        "title": "Legacy News Title",
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
    """Generated news summaries should enforce bounded native-output limits."""
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
                "key_points": ["Point one", "x" * 221],
                "summary": "Short overview.",
            }
        )

    with pytest.raises(ValidationError):
        GeneratedNewsSummary.model_validate(
            {
                "title": "Specific generated headline",
                "key_points": ["Point one", "Point two"],
                "summary": "x" * 501,
            }
        )


def test_generated_news_summary_accepts_natural_length_output() -> None:
    """Generated news summaries should allow readable prose instead of clipped fragments."""
    summary = GeneratedNewsSummary.model_validate(
        {
            "title": "Specific generated headline",
            "key_points": [
                "The first point can be a complete sentence with enough context to read naturally.",
                (
                    "The second point can explain why the development matters without "
                    "becoming a paragraph."
                ),
                "A third point can carry a concrete detail when the source supports it.",
                "A fourth point is allowed for richer source material.",
            ],
            "summary": (
                "The story explains a concrete development and why it matters to the market. "
                "It adds enough context for a reader to understand the stakes without opening "
                "the source immediately."
            ),
        }
    )

    assert len(summary.key_points) == 4
    assert summary.summary.startswith("The story explains")
