from app.models.contracts import BriefingBlockType, BriefingFigurePlacement, BriefingRunKind
from app.services.briefing.normalize import (
    markdown_to_narration,
    normalize_layout,
    source_keys_in_markdown,
    strip_insight_markers,
)


def test_normalize_layout_flattens_obsolete_insight_markers_to_text() -> None:
    layout = normalize_layout(
        [
            {
                "type": "passage",
                "weight": "feature",
                "markdown": (
                    "**Lead** [Story](newsly://briefing/content/7) "
                    "{{insight:why}}explains the useful claim.{{/insight}}"
                ),
            }
        ],
        source_keys={"content:7"},
    )

    assert layout.warnings == []
    assert layout.narration_text == "Lead Story explains the useful claim."
    block = layout.blocks[0]
    assert block["type"] == BriefingBlockType.PASSAGE.value
    runs = block["paragraphs"][0]["runs"]
    assert {run["kind"] for run in runs} == {
        BriefingRunKind.TEXT.value,
        BriefingRunKind.SOURCE_LINK.value,
    }
    assert any(run["text"] == "Lead" and run["bold"] for run in runs)
    assert any(run["text"] == "Story" and run["source_key"] == "content:7" for run in runs)
    assert all("insight_id" not in run for run in runs)
    assert "{{insight" not in layout.markdown_raw


def test_normalize_layout_strips_insight_markers_nested_in_source_links() -> None:
    layout = normalize_layout(
        [
            {
                "type": "passage",
                "markdown": (
                    "[A Redis use-after-free {{insight:memory}}memory corruption bug"
                    "{{/insight}}](newsly://briefing/news/456) was confirmed."
                ),
            }
        ],
        source_keys={"news:456"},
    )

    runs = layout.blocks[0]["paragraphs"][0]["runs"]
    source_link = next(run for run in runs if run["kind"] == "source_link")
    assert source_link["text"] == "A Redis use-after-free memory corruption bug"
    assert "{{insight" not in layout.markdown_raw


def test_normalize_layout_unwraps_bold_markers_around_source_links() -> None:
    layout = normalize_layout(
        [
            {
                "type": "passage",
                "weight": "feature",
                "markdown": (
                    "**[Linear Digressions](newsly://briefing/content/7)** is going quiet."
                ),
            }
        ],
        source_keys={"content:7"},
    )

    runs = layout.blocks[0]["paragraphs"][0]["runs"]
    assert not any("**" in run["text"] for run in runs)
    assert any(
        run["kind"] == BriefingRunKind.SOURCE_LINK.value and run["text"] == "Linear Digressions"
        for run in runs
    )


def test_normalize_layout_accepts_legacy_news_scheme_source_links() -> None:
    markdown = "[Story](news://briefing/news/8) explains the useful claim."
    layout = normalize_layout(
        [
            {
                "type": "passage",
                "markdown": markdown,
            }
        ],
        source_keys={"news:8"},
    )

    assert layout.warnings == []
    assert layout.narration_text == "Story explains the useful claim."
    assert source_keys_in_markdown(markdown) == {"news:8"}
    runs = layout.blocks[0]["paragraphs"][0]["runs"]
    assert any(run["kind"] == BriefingRunKind.SOURCE_LINK.value for run in runs)
    assert any(run["text"] == "Story" and run["source_key"] == "news:8" for run in runs)


def test_normalize_layout_preserves_full_figure_placement() -> None:
    layout = normalize_layout(
        [
            {
                "type": "figure",
                "source_key": "content:7",
                "placement": "full",
            }
        ],
        source_keys={"content:7"},
    )

    assert layout.blocks[0]["placement"] == BriefingFigurePlacement.FULL.value


def test_normalize_layout_defaults_invalid_figure_placement_to_inset() -> None:
    layout = normalize_layout(
        [
            {
                "type": "figure",
                "source_key": "content:7",
                "placement": "wide",
            }
        ],
        source_keys={"content:7"},
    )

    assert layout.blocks[0]["placement"] == BriefingFigurePlacement.INSET.value


def test_strip_insight_markers_and_source_key_extraction_are_stable() -> None:
    markdown = (
        "[One](newsly://briefing/news/3) starts. "
        "{{insight:open}}This marker has no close. Next sentence."
    )

    stripped = strip_insight_markers(markdown)

    assert "{{insight" not in stripped
    assert source_keys_in_markdown(markdown) == {"news:3"}
    assert markdown_to_narration(stripped) == (
        "One starts. This marker has no close. Next sentence."
    )
