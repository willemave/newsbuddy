from __future__ import annotations

from app.services.learning_deck_layout import (
    LEGACY_LEARNING_DECK_LAYOUT,
    RESPONSIVE_LEARNING_DECK_LAYOUT,
    learning_deck_prompt_values,
    learning_deck_viewer_profiles_json,
)


def test_responsive_layout_profile_owns_prompt_and_viewer_values() -> None:
    prompt_values = learning_deck_prompt_values()
    viewer_profiles = learning_deck_viewer_profiles_json()

    assert prompt_values == {
        "responsive_layout_meta_tag": ('<meta name="newsly-deck-layout" content="responsive-v2">'),
        "responsive_layout_version": "responsive-v2",
        "portrait_canvas": "720 × 1280",
        "landscape_canvas": "1280 × 720",
    }
    assert RESPONSIVE_LEARNING_DECK_LAYOUT.version in prompt_values["responsive_layout_meta_tag"]
    assert f'"width":{RESPONSIVE_LEARNING_DECK_LAYOUT.portrait.width}' in viewer_profiles
    assert f'"height":{RESPONSIVE_LEARNING_DECK_LAYOUT.portrait.height}' in viewer_profiles
    assert f'"height":{LEGACY_LEARNING_DECK_LAYOUT.portrait.height}' in viewer_profiles
