from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VIEWS_ROOT = REPO_ROOT / "client/newsly/newsly/Views"
ONBOARDING_ROOT = VIEWS_ROOT / "Onboarding"
ONBOARDING_FLOW = REPO_ROOT / "tests/ios_e2e/flows/onboarding_personalized.yaml"
MORE_SEARCH_CHAT_FLOW = REPO_ROOT / "tests/ios_e2e/flows/more_search_detail_chat_handoff.yaml"
TWEET_VOICE_FLOW = REPO_ROOT / "tests/ios_e2e/flows/tweet_fake_speech.yaml"
LEARNING_FOCUS_FLOW = REPO_ROOT / "tests/ios_e2e/flows/learning_deck_focus_fake_speech.yaml"


def test_landing_and_onboarding_screen_ids_live_on_title_leaves() -> None:
    landing_source = (VIEWS_ROOT / "LandingView.swift").read_text()
    flow_source = (ONBOARDING_ROOT / "OnboardingFlowView.swift").read_text()
    choice_source = (ONBOARDING_ROOT / "OnboardingChoiceStep.swift").read_text()

    assert landing_source.count('"auth.landing.screen"') == 1
    assert (
        ".appShadow(.titleGlow(glowColor))\n"
        '                    .accessibilityIdentifier("auth.landing.screen")'
    ) in landing_source
    assert '"onboarding.screen"' not in flow_source
    assert choice_source.count('"onboarding.choice.screen"') == 1
    assert (
        ".multilineTextAlignment(.center)\n"
        '                        .accessibilityIdentifier("onboarding.choice.screen")'
    ) in choice_source

    header_screen_ids = {
        "OnboardingAudioStep.swift": "onboarding.audio.screen",
        "OnboardingLoadingStep.swift": "onboarding.loading.screen",
        "OnboardingSuggestionsStep.swift": "onboarding.suggestions.screen",
        "OnboardingAggregatorsStep.swift": "onboarding.aggregators.screen",
        "OnboardingRedditStep.swift": "onboarding.reddit.screen",
    }
    for filename, identifier in header_screen_ids.items():
        source = (ONBOARDING_ROOT / filename).read_text()
        assert source.count(f'"{identifier}"') == 1
        assert f'titleAccessibilityIdentifier: "{identifier}"' in source
        assert f'.accessibilityIdentifier("{identifier}")' not in source


def test_onboarding_voice_controls_keep_distinct_leaf_ids() -> None:
    audio_source = (ONBOARDING_ROOT / "OnboardingAudioStep.swift").read_text()
    mic_source = (ONBOARDING_ROOT / "OnboardingMicButton.swift").read_text()

    assert '.accessibilityIdentifier("onboarding.audio.skip")' in audio_source
    assert '.accessibilityIdentifier("onboarding.audio.error")' in audio_source
    assert '.accessibilityIdentifier("onboarding.audio.mic")' in mic_source
    assert r'"onboarding.audio.state.\(audioState.accessibilityIdentifier)"' in mic_source


def test_product_screen_ids_live_on_stable_header_leaves() -> None:
    masthead_screens = {
        "Briefing/BriefingView.swift": "briefing.screen",
        "Briefing/BriefingEmptyStateView.swift": "briefing.screen",
        "KnowledgeView.swift": "knowledge.screen",
        "LearningView.swift": "learning.screen",
        "MoreView.swift": "more.screen",
    }
    for filename, identifier in masthead_screens.items():
        source = (VIEWS_ROOT / filename).read_text()
        assert source.count(f'"{identifier}"') == 1
        assert f'titleAccessibilityIdentifier: "{identifier}"' in source
        assert f'.accessibilityIdentifier("{identifier}")' not in source

    settings_source = (VIEWS_ROOT / "Settings" / "SettingsView.swift").read_text()
    assert settings_source.count('"settings.screen"') == 1
    assert 'accessibilityIdentifier: "settings.screen"' in settings_source
    assert '.accessibilityIdentifier("settings.screen")' not in settings_source

    detail_source = (VIEWS_ROOT / "ContentDetailView.swift").read_text()
    detail_header_source = (VIEWS_ROOT / "Components" / "DetailHeroHeader.swift").read_text()
    assert '"content.detail.screen"' not in detail_source
    assert detail_header_source.count('"content.detail.screen"') == 1
    assert (
        ".accessibilityElement(children: .ignore)\n"
        "        .accessibilityLabel(detailMetadataAccessibilityLabel)\n"
        '        .accessibilityIdentifier("content.detail.screen")'
    ) in detail_header_source

    article_source = (VIEWS_ROOT / "ArticleReaderView.swift").read_text()
    assert article_source.count('"article.reader.screen"') == 1
    assert (
        ".foregroundStyle(Color.onSurfaceSecondary)\n"
        '                .accessibilityIdentifier("article.reader.screen")'
    ) in article_source

    knowledge_source = (VIEWS_ROOT / "KnowledgeView.swift").read_text()
    assert knowledge_source.count('"knowledge.status.screen"') == 1
    assert (
        ".font(.terracottaHeadlineMedium)\n"
        '                    .accessibilityIdentifier("knowledge.status.screen")'
    ) in knowledge_source


def test_personalized_onboarding_flow_uses_stable_leaf_selectors() -> None:
    flow_source = ONBOARDING_FLOW.read_text()
    stable_ids = [
        "onboarding.choice.screen",
        "onboarding.choice.personalized",
        "onboarding.audio.screen",
        "onboarding.audio.state.recording",
        "onboarding.audio.mic",
        "onboarding.suggestions.screen",
        "onboarding.suggestions.continue",
        "onboarding.aggregators.screen",
        "onboarding.aggregators.continue",
        "onboarding.reddit.screen",
        "onboarding.complete",
        "briefing.screen",
    ]
    for identifier in stable_ids:
        assert f"id: {identifier}" in flow_source

    migrated_text_selectors = [
        "Personalize with voice",
        "Tell us what you read",
        "Tap again when you're done.",
        "Add news aggregators",
        "Add subreddit feeds",
        "Start with .* sources",
    ]
    for selector in migrated_text_selectors:
        assert f"text: {selector}" not in flow_source

    suggestion_ids = [
        "onboarding.suggestion.https://stratechery.com/feed",
        "onboarding.suggestion.https://www.latent.space/feed",
    ]
    for identifier in suggestion_ids:
        assert f'id: "{identifier}"' in flow_source
    assert "text: Stratechery" not in flow_source
    assert "text: Latent Space" not in flow_source


def test_more_search_and_voice_flows_use_stable_interaction_ids() -> None:
    more_search_source = MORE_SEARCH_CHAT_FLOW.read_text()
    for identifier in [
        "knowledge.more_menu",
        "more.screen",
        "more.search",
        "search.input",
        "content.detail.screen",
        "content.action.knowledge_actions",
        "content.knowledge_actions.sheet",
        "content.knowledge_actions.start_chat",
        "knowledge.chat_input",
    ]:
        assert f"id: {identifier}" in more_search_source

    tweet_source = TWEET_VOICE_FLOW.read_text()
    for identifier in [
        "content.action.share",
        "content.share.sheet",
        "content.share.tweet_suggestions",
        "content.tweet.sheet",
        "content.tweet.voice_mic",
    ]:
        assert f"id: {identifier}" in tweet_source
    assert "newslyE2EFakeSpeechEnabled: true" in tweet_source

    learning_source = LEARNING_FOCUS_FLOW.read_text()
    for identifier in [
        "content.action.knowledge_actions",
        "content.knowledge_actions.sheet",
        "content.knowledge_actions.learning_deck",
        "learning_deck.create.sheet",
        "learning_deck.focus_mic",
        "learning_deck.focus_recording",
        "learning_deck.create.focus",
    ]:
        assert f"id: {identifier}" in learning_source
    assert "newslyE2EFakeSpeechEnabled: true" in learning_source


def test_detail_sheet_ids_live_on_title_and_action_leaves() -> None:
    header_source = (VIEWS_ROOT / "Components" / "MiniSheetComponents.swift").read_text()
    knowledge_source = (VIEWS_ROOT / "Components" / "DetailKnowledgeActionsSheet.swift").read_text()
    mini_sheet_source = (VIEWS_ROOT / "Components" / "DetailMiniSheets.swift").read_text()

    assert "titleAccessibilityIdentifier: String" in header_source
    assert ".accessibilityIdentifier(titleAccessibilityIdentifier)" in header_source

    for source, screen_identifier in [
        (knowledge_source, "content.knowledge_actions.sheet"),
        (mini_sheet_source, "content.share.sheet"),
        (mini_sheet_source, "content.download.sheet"),
    ]:
        assert f'titleAccessibilityIdentifier: "{screen_identifier}"' in source
        assert f'.accessibilityIdentifier("{screen_identifier}")' not in source

    for identifier in [
        "content.knowledge_actions.start_chat",
        "content.knowledge_actions.council",
        "content.knowledge_actions.learning_deck",
        "content.share.title_link",
        "content.share.key_points",
        "content.share.full_content",
        "content.share.tweet_suggestions",
        "content.download.3",
        "content.download.5",
        "content.download.10",
        "content.download.20",
    ]:
        assert f'"{identifier}"' in knowledge_source + mini_sheet_source

    for removed_option in ["Dig deeper", "Deep Research", "Podcast overview"]:
        assert removed_option not in knowledge_source


def test_detail_learning_actions_use_the_learning_icon() -> None:
    action_bar_source = (VIEWS_ROOT / "Components" / "DetailActionBar.swift").read_text()
    learning_action_source = action_bar_source.split(
        "Button(action: onOpenKnowledgeActions)", maxsplit=1
    )[1].split('.accessibilityIdentifier("content.action.knowledge_actions")', maxsplit=1)[0]

    assert 'actionIcon("sparkles")' in learning_action_source
    assert 'actionIcon("books.vertical.fill")' not in learning_action_source
