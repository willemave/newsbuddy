from datetime import UTC, datetime

import pytest

from app.models.contracts import ContentType
from app.services.briefing.repair import BriefingLayoutRepairError, repair_layout
from app.services.briefing.sources import BriefingSource


def test_backfill_adds_inset_figure_after_citing_passage() -> None:
    sources = [_source(1), _source(2)]
    blocks = [
        _passage("[First](newsly://briefing/content/1) covers agents."),
        _passage("[Second](newsly://briefing/content/2) covers evals."),
    ]

    result = repair_layout(
        blocks,
        sources=sources,
        lens_key="articles",
        window_index=0,
        figure_budget=12,
        ensure_source_figures=True,
    )

    types = [block["type"] for block in result.blocks]
    assert types == ["passage", "figure", "passage", "figure"]
    first_figure = result.blocks[1]
    assert first_figure["source_key"] == "content:1"
    assert first_figure["placement"] == "inset"
    assert first_figure["image_url"] == "/static/images/content/1.png"
    assert first_figure["caption"] == "Article 1"
    assert "figure_backfill:2" in result.warnings


def test_backfill_skips_sources_already_figured_by_the_llm() -> None:
    sources = [_source(1), _source(2)]
    blocks = [
        _passage("[First](newsly://briefing/content/1) and [Second](newsly://briefing/content/2)."),
        {"type": "figure", "source_key": "content:1", "caption": "Model caption"},
    ]

    result = repair_layout(
        blocks,
        sources=sources,
        lens_key="articles",
        window_index=0,
        figure_budget=12,
        ensure_source_figures=True,
    )

    figures = [block for block in result.blocks if block["type"] == "figure"]
    assert [figure["source_key"] for figure in figures] == ["content:1", "content:2"]
    assert figures[0]["caption"] == "Model caption"
    assert "figure_backfill:1" in result.warnings


def test_backfill_skips_sources_without_images_and_respects_budget() -> None:
    sources = [_source(1), _source(2, imaged=False), _source(3), _source(4)]
    blocks = [
        _passage(
            " ".join(
                f"[Source {index}](newsly://briefing/content/{index}) develops a thesis."
                for index in (1, 2, 3, 4)
            )
        ),
    ]

    result = repair_layout(
        blocks,
        sources=sources,
        lens_key="articles",
        window_index=0,
        figure_budget=2,
        ensure_source_figures=True,
    )

    figures = [block for block in result.blocks if block["type"] == "figure"]
    assert [figure["source_key"] for figure in figures] == ["content:1", "content:3"]
    assert "figure_backfill:2" in result.warnings


def test_backfill_uses_coverage_repair_passage_for_uncited_sources() -> None:
    sources = [_source(1), _source(2)]
    blocks = [_passage("[First](newsly://briefing/content/1) stands alone.")]

    result = repair_layout(
        blocks,
        sources=sources,
        lens_key="articles",
        window_index=0,
        figure_budget=12,
        ensure_source_figures=True,
    )

    types = [block["type"] for block in result.blocks]
    assert types == ["passage", "figure", "passage", "figure"]
    assert result.blocks[3]["source_key"] == "content:2"
    assert "coverage_repair:1" in result.warnings


def test_layout_with_no_usable_passage_is_not_repaired() -> None:
    sources = [_source(1), _source(2)]
    blocks = [
        _passage("1.0"),
        _passage("0.0"),
        _passage("normal"),
        _passage("{{protocol}}1.0{{/protocol}}"),
    ]

    with pytest.raises(BriefingLayoutRepairError, match="requires regeneration"):
        repair_layout(
            blocks,
            sources=sources,
            lens_key="articles",
            window_index=0,
            figure_budget=12,
        )


def test_short_source_linked_passage_is_not_dropped() -> None:
    sources = [_source(1)]
    blocks = [_passage("[First](newsly://briefing/content/1) ships.")]

    result = repair_layout(
        blocks,
        sources=sources,
        lens_key="articles",
        window_index=0,
        figure_budget=12,
    )

    assert result.blocks[0]["markdown"] == "[First](newsly://briefing/content/1) ships."
    assert "low_signal_passage_dropped" not in result.warnings


def test_short_unlinked_prose_is_not_mistaken_for_schema_debris() -> None:
    sources = [_source(1)]
    blocks = [
        _passage("Why this matters now."),
        _passage("[First](newsly://briefing/content/1) ships."),
    ]

    result = repair_layout(
        blocks,
        sources=sources,
        lens_key="articles",
        window_index=0,
        figure_budget=12,
    )

    assert result.blocks[0]["markdown"] == "Why this matters now."
    assert "low_signal_passage_dropped" not in result.warnings


def test_low_signal_pullquotes_are_dropped() -> None:
    sources = [_source(1)]
    blocks = [
        _passage("[First](newsly://briefing/content/1) ships."),
        {"type": "pullquote", "source_key": "content:1", "text": "1.0"},
        {"type": "pullquote", "source_key": "content:1", "text": "optional"},
        {"type": "pullquote", "source_key": "content:1", "text": "content:29804"},
        {"type": "pullquote", "text": 'source_key": "news:18021",'},
    ]

    result = repair_layout(
        blocks,
        sources=sources,
        lens_key="articles",
        window_index=0,
        figure_budget=12,
    )

    assert [block["type"] for block in result.blocks] == ["passage"]
    assert result.warnings.count("low_signal_pullquote_dropped") == 4


def test_short_real_pullquote_is_not_dropped() -> None:
    sources = [_source(1)]
    blocks = [
        _passage("[First](newsly://briefing/content/1) ships."),
        {"type": "pullquote", "source_key": "content:1", "text": "Just learn SQL."},
    ]

    result = repair_layout(
        blocks,
        sources=sources,
        lens_key="articles",
        window_index=0,
        figure_budget=12,
    )

    assert [block["type"] for block in result.blocks] == ["passage", "pullquote"]
    assert result.blocks[1]["text"] == "Just learn SQL."
    assert "low_signal_pullquote_dropped" not in result.warnings


def test_full_placement_coerced_to_inset() -> None:
    sources = [_source(1)]
    blocks = [
        _passage("[First](newsly://briefing/content/1) covers agents."),
        {"type": "figure", "source_key": "content:1", "placement": "full"},
    ]

    result = repair_layout(
        blocks,
        sources=sources,
        lens_key="articles",
        window_index=0,
        figure_budget=12,
    )

    figures = [block for block in result.blocks if block["type"] == "figure"]
    assert [figure["placement"] for figure in figures] == ["inset"]


def test_em_dashes_replaced_across_block_types() -> None:
    sources = [_source(1)]
    blocks = [
        _passage(
            "[First](newsly://briefing/content/1) grew 2024—2026 — a striking run—by any measure."
        ),
        {
            "type": "figure",
            "source_key": "content:1",
            "caption": "Growth—and its limits.",
        },
        {"type": "pullquote", "source_key": "content:1", "text": "Momentum—not hype."},
    ]

    result = repair_layout(
        blocks,
        sources=sources,
        lens_key="articles",
        window_index=0,
        figure_budget=12,
    )

    passage, figure, pullquote = result.blocks
    assert "—" not in passage["markdown"]
    assert "2024-2026" in passage["markdown"]
    assert "run, by any measure" in passage["markdown"]
    assert figure["caption"] == "Growth, and its limits."
    assert pullquote["text"] == "Momentum, not hype."


def test_backfill_disabled_by_default() -> None:
    sources = [_source(1)]
    blocks = [_passage("[First](newsly://briefing/content/1) covers agents.")]

    result = repair_layout(
        blocks,
        sources=sources,
        lens_key="news-ai",
        window_index=0,
        figure_budget=6,
    )

    assert [block["type"] for block in result.blocks] == ["passage"]
    assert not any(warning.startswith("figure_backfill") for warning in result.warnings)


def _passage(markdown: str) -> dict[str, str]:
    return {"type": "passage", "weight": "feature", "markdown": markdown}


def _source(content_id: int, *, imaged: bool = True) -> BriefingSource:
    return BriefingSource(
        source_key=f"content:{content_id}",
        kind="content",
        id=content_id,
        tier="longform",
        lens_key="articles",
        title=f"Article {content_id}",
        summary="A concise summary.",
        key_points=["A concrete point."],
        url=f"https://example.com/{content_id}",
        image_url=f"/static/images/content/{content_id}.png" if imaged else None,
        thumbnail_url=f"/static/images/thumbnails/{content_id}.png" if imaged else None,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_type=ContentType.ARTICLE,
        briefing_context=None,
    )
