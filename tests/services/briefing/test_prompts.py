import pytest

from app.services.briefing.composer import LAYOUT_PROMPTS_BY_TIER, _layout_prompt_name
from app.services.prompt_library import render_prompt


@pytest.mark.parametrize("tier", ["audio", "longform", "news"])
def test_briefing_layout_prompt_sections_render(tier: str) -> None:
    prompt_name = LAYOUT_PROMPTS_BY_TIER[tier]
    system_prompt = render_prompt(f"{prompt_name}#system")
    user_prompt = render_prompt(
        f"{prompt_name}#window",
        lens_title="AI desk",
        source_payload_json="[]",
    )

    assert f"`{tier}` tier" in system_prompt
    assert "Never use em dashes" in system_prompt
    assert "Never open by naming the lens or counting its unread sources" in system_prompt
    if tier == "news":
        assert "roughly four to ten words" in system_prompt
    assert "newsly://briefing/" in system_prompt
    assert "news://briefing/" not in system_prompt
    assert "Lens: AI desk" in user_prompt
    assert f"Tier: {tier}" in user_prompt
    assert "Sources:\n\n[]" in user_prompt


@pytest.mark.parametrize("tier", ["audio", "longform"])
def test_deep_tier_prompts_demand_substantive_treatment(tier: str) -> None:
    prompt_name = LAYOUT_PROMPTS_BY_TIER[tier]
    system_prompt = render_prompt(f"{prompt_name}#system")
    user_prompt = render_prompt(
        f"{prompt_name}#window",
        lens_title="AI desk",
        source_payload_json="[]",
    )
    normalized_system_prompt = " ".join(system_prompt.split())

    assert "`figure`" in system_prompt
    assert "`suggested_quotes` as a separate top-level array" in system_prompt
    assert "not verbatim quotations or citations" in system_prompt
    assert "using only its `suggestion_id`" in normalized_system_prompt
    assert "exact provided `title`" in system_prompt
    assert "`source_name` is present" in normalized_system_prompt
    assert "Never invent" in system_prompt
    assert "target 3-5 sentences" in user_prompt


def test_news_tier_prompt_forbids_figures() -> None:
    system_prompt = render_prompt("briefing/layout_news#system")
    normalized_prompt = " ".join(system_prompt.split())

    assert "Return exactly one `passage` block" in system_prompt
    assert "Do not include `figure` or `pullquote` blocks" in system_prompt
    assert "Add a `pullquote`" not in system_prompt
    assert "Write exactly one compact paragraph of at most three sentences" in system_prompt
    assert "link every provided source exactly once" in normalized_prompt


def test_layout_prompt_name_rejects_unknown_tier() -> None:
    with pytest.raises(ValueError, match="No briefing layout prompt"):
        _layout_prompt_name("video")


def test_briefing_auxiliary_prompt_sections_render() -> None:
    lens_prompt = render_prompt("briefing/lens_naming#user", source_payload_json="[]")
    masthead_prompt = render_prompt(
        "briefing/masthead#user",
        current_deck="Previous deck.",
        source_titles="A title",
    )

    assert lens_prompt == "[]"
    assert "Previous deck." in masthead_prompt
    assert "A title" in masthead_prompt
