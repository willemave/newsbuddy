from app.models.contracts import BriefingBlockType, BriefingRunKind
from app.services.briefing.normalize import (
    close_unpaired_insights,
    markdown_to_narration,
    normalize_layout,
    source_keys_in_markdown,
)


def test_normalize_layout_parses_source_links_and_insight_runs() -> None:
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
        BriefingRunKind.INSIGHT.value,
    }
    assert any(run["text"] == "Lead" and run["bold"] for run in runs)
    assert any(run["text"] == "Story" and run["source_key"] == "content:7" for run in runs)
    assert any(run["insight_id"] == "why" for run in runs)


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


def test_close_unpaired_insights_and_source_key_extraction_are_stable() -> None:
    markdown = (
        "[One](newsly://briefing/news/3) starts. "
        "{{insight:open}}This marker has no close. Next sentence."
    )

    closed = close_unpaired_insights(markdown)

    assert "{{/insight}}" in closed
    assert source_keys_in_markdown(markdown) == {"news:3"}
    assert markdown_to_narration(closed) == ("One starts. This marker has no close. Next sentence.")
