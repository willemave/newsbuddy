import json
from datetime import UTC, datetime

import pytest

from app.models.contracts import ContentType
from app.services.briefing.composer import (
    _blocks_look_malformed,
    _parse_composer_layout_json,
    _source_payload,
    compose_window,
)
from app.services.briefing.sources import BriefingSource

MALFORMED_BLOCKS = [
    {"type": "passage", "weight": "placeholder_gibberish_with_all_the_prose"},
    {"type": "figure", "weight": "source_key_markdown_placeholder_ignore"},
]
WELL_FORMED_BLOCKS = [
    {
        "type": "passage",
        "weight": "feature",
        "markdown": "[A useful article](newsly://briefing/content/1) explains the thesis.",
    }
]


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


def test_blocks_look_malformed_detects_weight_dump() -> None:
    assert _blocks_look_malformed(MALFORMED_BLOCKS) is True
    assert _blocks_look_malformed([]) is True
    assert _blocks_look_malformed(WELL_FORMED_BLOCKS) is False
    assert _blocks_look_malformed([{"type": "figure", "source_key": "content:1"}]) is False


def test_compose_window_retries_once_on_malformed_blocks(monkeypatch) -> None:
    attempts: list[int] = []

    def fake_llm(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        blocks = MALFORMED_BLOCKS if len(attempts) == 1 else WELL_FORMED_BLOCKS
        return blocks, None

    monkeypatch.setattr("app.services.briefing.composer._compose_window_with_llm", fake_llm)

    segment = compose_window(
        [_source()],
        lens_key="articles",
        lens_title="Articles",
        tier="longform",
        window_index=1,
        use_llm=True,
    )

    assert len(attempts) == 2
    assert "llm_malformed_retry:1" in segment.warnings
    assert segment.blocks


def test_compose_window_falls_back_when_malformed_blocks_persist(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.briefing.composer._compose_window_with_llm",
        lambda *_args, **_kwargs: (MALFORMED_BLOCKS, None),  # noqa: ANN002, ANN003
    )

    segment = compose_window(
        [_source()],
        lens_key="articles",
        lens_title="Articles",
        tier="longform",
        window_index=1,
        use_llm=True,
    )

    assert segment.model == "deterministic"
    assert "llm_malformed_layout_fallback" in segment.warnings
    assert segment.blocks


def test_compose_window_falls_back_when_layout_json_stays_invalid(monkeypatch) -> None:
    attempts: list[int] = []

    def invalid_json(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        raise json.JSONDecodeError("Unterminated string", '{"blocks":[{"type":"passage"', 21)

    monkeypatch.setattr("app.services.briefing.composer._compose_window_with_llm", invalid_json)

    segment = compose_window(
        [_source()],
        lens_key="articles",
        lens_title="Articles",
        tier="longform",
        window_index=1,
        use_llm=True,
    )

    assert len(attempts) == 2
    assert segment.model == "deterministic"
    assert "llm_invalid_layout_retry:1" in segment.warnings
    assert "llm_invalid_layout_fallback:JSONDecodeError" in segment.warnings
    assert segment.blocks


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


def test_parse_composer_layout_json_coerces_passage_content_field() -> None:
    layout = _parse_composer_layout_json(
        '{"blocks":[{"type":"passage","weight":"feature","content":"A useful brief."}]}'
    )

    assert len(layout.blocks) == 1
    assert layout.blocks[0].type == "passage"
    assert layout.blocks[0].markdown == "A useful brief."


def test_parse_composer_layout_json_coerces_non_passage_content_fields() -> None:
    layout = _parse_composer_layout_json(
        """
        {
          "blocks": [
            {"type": "pullquote", "content": "A concise quote."},
            {"type": "figure", "source_key": "content:1", "content": "A figure caption."}
          ]
        }
        """
    )

    assert layout.blocks[0].text == "A concise quote."
    assert layout.blocks[1].caption == "A figure caption."


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
