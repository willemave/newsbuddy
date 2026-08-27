from __future__ import annotations

from sqlalchemy import text

from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content


def test_content_metadata_is_sanitized_before_persistence(db_session) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/control-characters",
        title="Control characters",
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "summary": {
                "title": "Broken\u0000 title\u0001",
                "literal": r"Keep \u0000 text",
                "formatted": "Line one\nLine two\tTabbed",
            },
            "items": ("valid", "also\u0007 valid"),
            "non_string_keys": {False: "false", None: "none", 2: "two"},
        },
    )
    db_session.add(content)
    db_session.commit()

    persisted = (
        db_session.execute(
            text(
                """
            SELECT
                content_metadata -> 'summary' ->> 'title' AS title,
                content_metadata -> 'summary' ->> 'literal' AS literal,
                content_metadata -> 'summary' ->> 'formatted' AS formatted,
                content_metadata -> 'items' AS items,
                content_metadata -> 'non_string_keys' AS non_string_keys
            FROM contents
            WHERE id = :content_id
            """
            ),
            {"content_id": content.id},
        )
        .mappings()
        .one()
    )

    assert persisted["title"] == "Broken title"
    assert persisted["literal"] == r"Keep \u0000 text"
    assert persisted["formatted"] == "Line one\nLine two\tTabbed"
    assert persisted["items"] == ["valid", "also valid"]
    assert persisted["non_string_keys"] == {"false": "false", "null": "none", "2": "two"}

    matched = (
        db_session.query(Content)
        .filter(Content.content_metadata["summary"]["title"].as_string() == "Broken title")
        .one()
    )
    assert matched.id == content.id
    assert Content.__table__.c.content_metadata.type.python_type is dict
