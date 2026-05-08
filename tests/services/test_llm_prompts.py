from app.services.llm_prompts import generate_summary_prompt


def test_news_prompt_uses_short_hard_caps() -> None:
    system_prompt, _ = generate_summary_prompt("news", max_bullet_points=4, max_quotes=0)

    assert "matching the provided structured output schema" in system_prompt
    assert "Field guidance:" in system_prompt
    assert "title: direct factual headline, <=95 characters" in system_prompt
    assert "key_points: include 2-3 self-contained bullets" in system_prompt
    assert "<=120 characters each" in system_prompt
    assert "one-sentence overview, <=180 characters" in system_prompt
    assert '"title"' not in system_prompt
    assert "Return a JSON object" not in system_prompt


def test_editorial_prompt_uses_short_hard_caps() -> None:
    system_prompt, _ = generate_summary_prompt(
        "editorial_narrative",
        max_bullet_points=10,
        max_quotes=4,
    )

    assert "matching the provided structured output schema" in system_prompt
    assert "Field guidance:" in system_prompt
    assert "editorial_narrative: one compact thesis-led paragraph, 90-150 words" in system_prompt
    assert "quotes: include exactly 2 direct quotes" in system_prompt
    assert "key_points: include 4-6 non-overlapping points" in system_prompt
    assert "each <=22 words" in system_prompt
    assert '"editorial_narrative"' not in system_prompt
    assert "Return a JSON object" not in system_prompt
