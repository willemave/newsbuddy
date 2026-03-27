"""Tests for chat process summary label formatting."""

from app.routers.api.chat import _format_process_summary_label


def test_format_process_summary_label_includes_tool_count_for_search() -> None:
    """Search-backed summaries should expose the number of executed tools."""
    label = _format_process_summary_label(
        ["exa_web_search", "exa_web_search", "other_tool"],
        has_intermediate_assistant_text=False,
    )

    assert label == "Thinking • Executed 3 tools and reviewed sources"


def test_format_process_summary_label_includes_tool_count_for_non_search_tools() -> None:
    """Generic tool usage should expose the number of executed tools."""
    label = _format_process_summary_label(
        ["feed_lookup"],
        has_intermediate_assistant_text=False,
    )

    assert label == "Thinking • Executed 1 tool and reviewed results"
