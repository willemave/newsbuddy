from datetime import UTC, datetime

import pytest

from app.models.contracts import ContentType
from app.services.briefing.composer import (
    _parse_composer_layout_json,
    _source_payload,
    compose_window,
)
from app.services.briefing.sources import BriefingSource


def test_compose_window_raises_llm_errors_without_deterministic_fallback(monkeypatch) -> None:
    def fail_llm(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise TimeoutError("model stalled")

    monkeypatch.setattr("app.services.briefing.composer._compose_window_with_llm", fail_llm)

    with pytest.raises(TimeoutError, match="model stalled"):
        compose_window(
            [_source()],
            lens_key="articles",
            lens_title="Articles",
            tier="longform",
            window_index=1,
            use_llm=True,
        )


def test_parse_composer_layout_json_accepts_object_wrapper() -> None:
    layout = _parse_composer_layout_json(
        '{"blocks":[{"type":"passage","weight":"feature","markdown":"A useful brief."}]}'
    )

    assert len(layout.blocks) == 1
    assert layout.blocks[0].type == "passage"
    assert layout.blocks[0].markdown == "A useful brief."


def test_parse_composer_layout_json_accepts_root_block_array() -> None:
    layout = _parse_composer_layout_json(
        '[{"type":"passage","weight":"feature","markdown":"A useful brief."}]'
    )

    assert len(layout.blocks) == 1
    assert layout.blocks[0].type == "passage"
    assert layout.blocks[0].markdown == "A useful brief."


def test_parse_composer_layout_json_accepts_fenced_root_block_array() -> None:
    layout = _parse_composer_layout_json(
        '```json\n[{"type":"passage","weight":"feature","markdown":"A useful brief."}]\n```'
    )

    assert len(layout.blocks) == 1
    assert layout.blocks[0].type == "passage"
    assert layout.blocks[0].markdown == "A useful brief."


def test_source_payload_includes_briefing_context_when_available() -> None:
    payload = _source_payload(_source_with_context("Long-form source detail."))

    assert payload["briefing_context"] == "Long-form source detail."


def _source() -> BriefingSource:
    return _source_with_context(None)


def _source_with_context(briefing_context: str | None) -> BriefingSource:
    return BriefingSource(
        source_key="content:1",
        kind="content",
        id=1,
        tier="longform",
        lens_key="articles",
        title="A useful article",
        summary="A concise summary.",
        key_points=["A concrete point."],
        url="https://example.com/article",
        image_url=None,
        thumbnail_url=None,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_type=ContentType.ARTICLE,
        briefing_context=briefing_context,
    )
