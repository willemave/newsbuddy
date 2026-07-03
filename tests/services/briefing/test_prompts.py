from app.services.prompt_library import render_prompt


def test_briefing_layout_prompt_sections_render() -> None:
    system_prompt = render_prompt("briefing/layout#system", tier="news")
    user_prompt = render_prompt(
        "briefing/layout#window",
        lens_title="AI desk",
        tier="news",
        source_payload_json="[]",
    )

    assert "`news` tier" in system_prompt
    assert "Lens: AI desk" in user_prompt
    assert "Sources:\n\n[]" in user_prompt


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
