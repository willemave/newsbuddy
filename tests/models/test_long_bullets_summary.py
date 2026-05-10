from typing import cast

from app.models.contracts import ContentStatus, ContentType
from app.models.domain.content import ContentData


def _build_points(count: int) -> list[dict[str, object]]:
    points = []
    for idx in range(count):
        points.append(
            cast(
                dict[str, object],
                {
                    "text": f"Point {idx + 1} covers a main idea in one sentence.",
                    "detail": (
                        f"Detail for point {idx + 1} adds supporting evidence and context. "
                        "It includes implications and concrete specifics for the reader."
                    ),
                    "quotes": [
                        {
                            "text": f"This is a supporting quote for point {idx + 1}.",
                            "context": "Source context",
                        }
                    ],
                },
            )
        )
    return points


def test_long_bullets_summary_points_and_quotes():
    metadata = {
        "summary_kind": "long_bullets",
        "summary_version": 1,
        "summary": {
            "title": "Example Title",
            "points": _build_points(10),
            "classification": "to_read",
            "summarization_date": "2026-02-04T00:00:00Z",
        },
    }

    content = ContentData(
        id=1,
        content_type=ContentType.ARTICLE,
        url="https://example.com",
        status=ContentStatus.COMPLETED,
        metadata=metadata,
    )

    bullet_points = content.bullet_points
    assert len(bullet_points) == 10
    assert bullet_points[0]["text"].startswith("Point 1")

    quotes = content.quotes
    assert len(quotes) == 10
    assert quotes[0]["text"].startswith("This is a supporting quote")
