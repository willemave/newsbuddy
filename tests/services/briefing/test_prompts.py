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
    assert "roughly four to ten words" in system_prompt
    assert "in the first paragraph, toward the beginning" in system_prompt
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

    assert "`figure`" in system_prompt
    assert "target 3-5 sentences" in user_prompt


def test_news_tier_prompt_forbids_figures() -> None:
    system_prompt = render_prompt("briefing/layout_news#system")

    assert "Do not include `figure` blocks" in system_prompt


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
