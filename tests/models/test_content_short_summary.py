import pytest

from app.models.schema import Content


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ({"overview": "Short overview"}, "Short overview"),
        ({"hook": "Interleaved hook"}, "Interleaved hook"),
        ({"summary": "Daily summary"}, "Daily summary"),
        ({"points": [{"text": "First bullet point summary"}]}, "First bullet point summary"),
        (
            {"editorial_narrative": "Editorial first paragraph.\n\nEditorial second paragraph."},
            "Editorial first paragraph.",
        ),
        ("Plain summary", "Plain summary"),
    ],
)
def test_content_short_summary(summary, expected):
    content = Content(
        content_type="article",
        url="https://example.com",
        content_metadata={
            "summary": summary,
            "summary_kind": "long_interleaved",
            "summary_version": 2,
        },
    )

    assert content.short_summary == expected


def test_content_short_summary_missing_metadata():
    content = Content(
        content_type="article",
        url="https://example.com",
        content_metadata={},
    )

    assert content.short_summary is None
