import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "client/newsly/newsly"
VIEWS_ROOT = REPO_ROOT / "client/newsly/newsly/Views"
DESIGN_TOKENS = VIEWS_ROOT / "Shared/DesignTokens.swift"


def test_ios_screen_margin_has_single_source_of_truth() -> None:
    source = DESIGN_TOKENS.read_text()

    assert "static let appHorizontalMargin: CGFloat = 20" in source
    for alias in (
        "screenHorizontal",
        "fastReadHorizontal",
        "readerHorizontal",
        "chatHorizontal",
    ):
        assert f"static let {alias}: CGFloat = appHorizontalMargin" in source


def test_ios_views_use_shared_margin_token_for_screen_gutters() -> None:
    old_aliases = re.compile(
        r"Spacing\.(screenHorizontal|fastReadHorizontal|readerHorizontal|chatHorizontal)"
    )
    offenders: list[str] = []

    for path in sorted(VIEWS_ROOT.rglob("*.swift")):
        if path == DESIGN_TOKENS:
            continue
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if old_aliases.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_primary_screens_do_not_hardcode_large_horizontal_gutters() -> None:
    checked_paths = [
        *sorted(VIEWS_ROOT.glob("*.swift")),
        *sorted((VIEWS_ROOT / "Onboarding").glob("*.swift")),
        VIEWS_ROOT / "Components/ChatStatusBanner.swift",
    ]
    hardcoded_outer_gutter = re.compile(r"\.padding\(\.horizontal,\s*(20|24|28|40)\)")
    offenders: list[str] = []

    for path in checked_paths:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if hardcoded_outer_gutter.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_generic_black_shadows_use_design_tokens() -> None:
    generic_black_shadow = re.compile(r"\.shadow\(color:\s*(?:Color\.)?\.?black")
    exempt_paths = {
        DESIGN_TOKENS,
        VIEWS_ROOT / "Briefing/BriefingView.swift",
    }
    offenders: list[str] = []

    for path in sorted(VIEWS_ROOT.rglob("*.swift")):
        if path in exempt_paths or path.parent == VIEWS_ROOT / "Briefing":
            continue
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if generic_black_shadow.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_swiftui_shadows_use_shadow_style_tokens() -> None:
    exempt_paths = {
        DESIGN_TOKENS,
        VIEWS_ROOT / "Briefing/BriefingView.swift",
    }
    offenders: list[str] = []

    for path in sorted(VIEWS_ROOT.rglob("*.swift")):
        if path in exempt_paths:
            continue
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if ".shadow(" in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_swiftui_motion_uses_app_motion_tokens() -> None:
    direct_motion_tokens = [
        "withAnimation(.spring",
        "withAnimation(.ease",
        "withAnimation(.linear",
        ".animation(.spring",
        ".animation(.ease",
        ".animation(.linear",
        "Animation.spring",
        "Animation.ease",
        "Animation.linear",
        ".spring(duration:",
        ".easeInOut(duration:",
        ".easeOut(duration:",
        ".linear(duration:",
    ]
    exempt_paths = {
        DESIGN_TOKENS,
        VIEWS_ROOT / "Briefing/BriefingView.swift",
    }
    offenders: list[str] = []

    for path in sorted(VIEWS_ROOT.rglob("*.swift")):
        if path in exempt_paths or path.parent == VIEWS_ROOT / "Briefing":
            continue
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if any(token in line for token in direct_motion_tokens):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_swiftui_views_use_sensory_feedback_for_haptics() -> None:
    manual_haptic = re.compile(r"UI(?:Impact|Notification|Selection)FeedbackGenerator")
    offenders: list[str] = []

    for path in sorted(VIEWS_ROOT.rglob("*.swift")):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if manual_haptic.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_detail_loading_state_uses_shared_skeleton_rows() -> None:
    skeleton_source = VIEWS_ROOT / "Shared/SkeletonViews.swift"
    source = skeleton_source.read_text()

    assert "struct SkeletonRow" in source
    assert "struct ContentDetailSkeletonView" in source
    assert "SkeletonCard" not in source
    assert "SkeletonFeedList" not in source
    assert "ContentDetailSkeletonView()" in (VIEWS_ROOT / "ContentDetailView.swift").read_text()


def test_cached_async_image_call_sites_pass_target_size() -> None:
    exempt_paths = {
        VIEWS_ROOT / "Briefing/BriefingView.swift",
        VIEWS_ROOT / "Components/CachedAsyncImage.swift",
    }
    offenders: list[str] = []

    for path in sorted(VIEWS_ROOT.rglob("*.swift")):
        if path in exempt_paths:
            continue
        lines = path.read_text().splitlines()
        for index, line in enumerate(lines):
            if "CachedAsyncImage(" not in line:
                continue
            call_window = "\n".join(lines[index : index + 12])
            if "targetSize:" not in call_window:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{index + 1}: missing targetSize")

    assert offenders == []


def test_empty_and_error_states_share_state_view() -> None:
    empty_state_source = (VIEWS_ROOT / "Shared/EmptyStateView.swift").read_text()
    error_source = (VIEWS_ROOT / "Components/ErrorView.swift").read_text()

    assert "struct StateView" in empty_state_source
    assert "enum Role" in empty_state_source
    assert "role: .empty" in empty_state_source
    assert "role: .error" in error_source


def test_content_text_size_reaches_briefing_detail_and_chat() -> None:
    root_tabs = (VIEWS_ROOT / "RootTabs.swift").read_text()
    content_routes = (VIEWS_ROOT / "ContentRoutes.swift").read_text()
    history_view = (VIEWS_ROOT / "ChatSessionHistoryView.swift").read_text()

    required_snippets = [
        "ContentDetailView(",
        "ChatSessionView(",
        "ChatSessionHistoryView(",
    ]

    for snippet in required_snippets:
        assert snippet in content_routes

    assert "BriefingView(viewModel: viewModel" in root_tabs
    assert ".dynamicTypeSize(contentTextSize)" in root_tabs
    assert content_routes.count(".dynamicTypeSize(contentTextSize)") >= 3
    assert "AppTextSize(index: settings.appTextSizeIndex)" not in history_view


def test_learning_view_uses_model_destinations() -> None:
    learning_source = (VIEWS_ROOT / "LearningView.swift").read_text()

    assert (
        "@State private var deckReaderDestination: LearningDeckReaderDestination?"
        in learning_source
    )
    assert ".fullScreenCover(item: $deckReaderDestination)" in learning_source
    assert "LearningFocusRequest" not in learning_source
    assert "CustomNarrationListSheet" not in learning_source
    assert "DispatchQueue.main.async" not in learning_source


def test_settings_sheets_use_single_model_destination() -> None:
    source = (VIEWS_ROOT / "Settings/SettingsView.swift").read_text()

    assert "private enum SettingsSheetDestination" in source
    assert "@State private var activeSheet: SettingsSheetDestination?" in source
    assert ".sheet(item: $activeSheet)" in source
    assert "showingFeedbackSheet" not in source
    assert "showingDebugMenu" not in source
    assert "showingCLILinkScanner" not in source
    assert "DispatchQueue.main.async" not in source
    assert "await Task.yield()" in source
    assert "activeSheet = nil" in source


def test_source_settings_sheets_use_single_model_destinations() -> None:
    source_views = {
        "FeedSourcesView.swift": "FeedSourcesSheetDestination",
        "PodcastSourcesView.swift": "PodcastSourcesSheetDestination",
    }

    for filename, destination_name in source_views.items():
        source = (VIEWS_ROOT / "Sources" / filename).read_text()

        assert f"private enum {destination_name}" in source
        assert f"@State private var activeSheet: {destination_name}?" in source
        assert "case addSource" in source
        assert "case sourceDetail(ScraperConfig)" in source
        assert ".sheet(item: $activeSheet)" in source
        assert ".sheet(isPresented: $showAddSheet)" not in source
        assert ".sheet(item: $selectedConfig)" not in source
        assert "selectedConfig" not in source
        assert "showAddSheet" not in source


def test_content_detail_sheets_use_single_model_destination() -> None:
    detail_source = (VIEWS_ROOT / "ContentDetailView.swift").read_text()
    presentation_source = (
        VIEWS_ROOT / "Components/ContentDetailPresentationModels.swift"
    ).read_text()

    assert "enum DetailSheetDestination" in presentation_source
    assert "case learningDeckCreate" in presentation_source
    assert "@State private var activeSheet: DetailSheetDestination?" in detail_source
    assert ".sheet(item: $activeSheet" in detail_source
    assert "activeSheet = .learningDeckCreate" in detail_source
    assert ".sheet(isPresented: $showLearningDeckCreateSheet)" not in detail_source
    assert "showLearningDeckCreateSheet" not in detail_source


def test_chat_session_sheets_use_single_model_destination() -> None:
    source = (VIEWS_ROOT / "ChatSessionView.swift").read_text()

    assert "private enum ChatSessionSheetDestination" in source
    assert "@State private var activeSheet: ChatSessionSheetDestination?" in source
    assert "case councilSettings" in source
    assert "case share(ShareContent)" in source
    assert ".sheet(item: $activeSheet)" in source
    assert "activeSheet = .councilSettings" in source
    assert "activeSheet = .share(" in source
    assert "isCouncilSettingsPresented" not in source
    assert "shareContent" not in source


def test_primary_scroll_surfaces_use_top_edge_fade() -> None:
    expected_usages = {
        VIEWS_ROOT / "KnowledgeView.swift": ".topScreenEdgeFade()",
        VIEWS_ROOT / "LearningView.swift": ".topScreenEdgeFade()",
        VIEWS_ROOT / "ChatSessionHistoryView.swift": ".topScreenEdgeFade()",
    }
    offenders: list[str] = []

    for path, usage in expected_usages.items():
        if usage not in path.read_text():
            offenders.append(f"{path.relative_to(REPO_ROOT)} missing {usage}")

    assert offenders == []


def test_cached_async_image_fades_use_motion_tokens() -> None:
    source = (VIEWS_ROOT / "Components/CachedAsyncImage.swift").read_text()
    components_docs = (REPO_ROOT / "docs/codebase/client/81-views-components.md").read_text()

    assert "@Environment(\\.accessibilityReduceMotion) private var reduceMotion" in source
    assert source.count("AppMotion.respectingReduceMotion(reduceMotion, AppMotion.subtle)") == 2
    assert ".easeIn(duration:" not in source
    assert ".easeOut(duration:" not in source
    assert "CachedAsyncImage" in components_docs
    assert "image fades use `AppMotion.subtle`" in components_docs


def test_knowledge_ready_content_projection_is_computed_once_per_render() -> None:
    source = (VIEWS_ROOT / "KnowledgeView.swift").read_text()

    projection = "let readyContentIDs = viewModel.contents.compactMap"
    assert source.count(projection) == 2
    assert source.index(projection) < source.index("ForEach(viewModel.contents)")
    assert "private var readyContentIDs" not in source


def test_chat_messages_use_single_parameterized_bubble_surface() -> None:
    chat_root = VIEWS_ROOT / "Chat"
    message_bubble = (chat_root / "MessageBubble.swift").read_text()
    message_list = (chat_root / "ChatMessageList.swift").read_text()
    chat_docs = (REPO_ROOT / "docs/codebase/client/87-views-chat.md").read_text()

    assert not (chat_root / "UserMessageBubble.swift").exists()
    assert not (chat_root / "AssistantMessageBubble.swift").exists()
    assert "struct MessageBubbleChrome" in message_bubble
    assert "struct MessageBubbleStyle" in message_bubble
    assert "UserMessageBubble" not in message_bubble
    assert "AssistantMessageBubble" not in message_bubble
    assert ".transition(messageInsertionTransition)" in message_list
    assert "AppMotion.respectingReduceMotion(reduceMotion, AppMotion.subtle)" in message_list
    assert ".animation(messageAnimation, value: timeline.last?.id)" in message_list
    assert ".animation(messageAnimation, value: timeline.map(\\.id))" not in message_list
    assert ".animation(messageAnimation, value: isSending)" in message_list
    assert ".defaultScrollAnchor(.bottom)" in message_list
    assert ".topScreenEdgeFade()" not in message_list
    assert "parameterized user/assistant bubble presentation" in chat_docs


def test_ios_timing_hacks_use_completion_or_task_boundaries() -> None:
    offenders: list[str] = []

    for path in sorted(APP_ROOT.rglob("*.swift")):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if "DispatchQueue.main.asyncAfter" in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    presenter_source = (APP_ROOT / "ViewModels/ActivityViewPresenter.swift").read_text()
    root_tabs_source = (VIEWS_ROOT / "RootTabs.swift").read_text()

    assert offenders == []
    assert "transitionCoordinator.animate(alongsideTransition: nil)" in presenter_source
    assert "await Task.yield()" in presenter_source
    assert root_tabs_source.count("guard path.wrappedValue.isEmpty else") == 1
    assert "pushBriefingContentDetail(route, path: $path)" in root_tabs_source


def test_swipe_snapback_uses_motion_tokens() -> None:
    checked_paths = [
        VIEWS_ROOT / "ContentDetailSwipeOverlay.swift",
        VIEWS_ROOT / "ChatSessionView.swift",
    ]

    for path in checked_paths:
        source = path.read_text()
        assert ".interactiveSpring(" not in source
        assert "AppMotion.press" in source


def test_shared_pressable_button_style_uses_press_motion_token() -> None:
    source = (VIEWS_ROOT / "Shared/PressableButtonStyle.swift").read_text()
    shared_docs = (REPO_ROOT / "docs/codebase/client/84-views-shared.md").read_text()
    pressed_scale = re.compile(r"\.scaleEffect\(configuration\.isPressed[^\n]*\)")
    offenders: list[str] = []

    for path in sorted(VIEWS_ROOT.rglob("*.swift")):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if pressed_scale.search(line) and "0.96" not in line and "pressedScale" not in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert "var pressedScale: CGFloat = 0.96" in source
    assert ".animation(AppMotion.press, value: configuration.isPressed)" in source
    assert ".spring(" not in source
    assert offenders == []
    assert "PressableButtonStyle" in shared_docs
    assert "AppMotion.press" in shared_docs


def test_shared_controls_use_motion_tokens_for_common_state_animation() -> None:
    checked_paths = [
        VIEWS_ROOT / "Components/TapToTalkMicButton.swift",
        VIEWS_ROOT / "Chat/ChatComposerDock.swift",
        VIEWS_ROOT / "Components/LearningDeckChatComposer.swift",
        VIEWS_ROOT / "Components/LearningDeckChatPanel.swift",
        VIEWS_ROOT / "Components/ToastView.swift",
        VIEWS_ROOT / "Components/DetailChatSheet.swift",
        VIEWS_ROOT / "Components/MiniSheetComponents.swift",
        VIEWS_ROOT / "Chat/MessageBubble.swift",
        VIEWS_ROOT / "ChatSessionView.swift",
        VIEWS_ROOT / "Chat/AssistantFeedOptionsSection.swift",
        VIEWS_ROOT / "Settings/SettingsView.swift",
        VIEWS_ROOT / "Components/StructuredSummaryView.swift",
        VIEWS_ROOT / "Components/ExpandableSection.swift",
        VIEWS_ROOT / "Components/LearningDeckReaderView.swift",
        VIEWS_ROOT / "Components/CommunityDiscussionSummarySection.swift",
        VIEWS_ROOT / "KnowledgeView.swift",
        VIEWS_ROOT / "RecentlyReadView.swift",
        VIEWS_ROOT / "Onboarding/OnboardingFlowView.swift",
        VIEWS_ROOT / "Onboarding/OnboardingProgressHeader.swift",
        VIEWS_ROOT / "Onboarding/OnboardingMicButton.swift",
        VIEWS_ROOT / "Onboarding/OnboardingAggregatorsStep.swift",
        VIEWS_ROOT / "Onboarding/OnboardingRedditStep.swift",
        VIEWS_ROOT / "Onboarding/OnboardingSuggestionsStep.swift",
        VIEWS_ROOT / "Onboarding/OnboardingChoiceStep.swift",
        VIEWS_ROOT / "Onboarding/OnboardingLoadingStep.swift",
        APP_ROOT / "ViewModels/ContentListViewModel.swift",
    ]
    banned_common_animation = re.compile(
        r"\.(?:easeOut|easeInOut)\(duration:\s*0\.(?:12|14|18|2|25|3)\b"
        r"|\.spring\(\)"
        r"|\.spring\(response:\s*0\.24"
    )
    offenders: list[str] = []

    for path in checked_paths:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if banned_common_animation.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_long_running_pulse_views_respect_reduce_motion() -> None:
    checked_paths = [
        VIEWS_ROOT / "Chat/ChatActivityViews.swift",
        VIEWS_ROOT / "Chat/ChatComposerDock.swift",
        VIEWS_ROOT / "Components/ChatStatusBanner.swift",
        VIEWS_ROOT / "Components/ChatLoadingView.swift",
        VIEWS_ROOT / "Onboarding/OnboardingMicButton.swift",
        VIEWS_ROOT / "Onboarding/OnboardingLoadingStep.swift",
    ]
    chat_docs = (REPO_ROOT / "docs/codebase/client/87-views-chat.md").read_text()
    onboarding_docs = (REPO_ROOT / "docs/codebase/client/82-views-onboarding.md").read_text()
    design_tokens = DESIGN_TOKENS.read_text()

    for token in (
        "recordingPulse",
        "finalizingPulse",
        "voiceLevelPulse",
        "typingDotPulse",
        "loadingBubblePulse",
        "chatStatusPulse",
        "chatIllustrationPulse",
    ):
        assert f"static let {token} = Animation." in design_tokens
        assert (
            f"static let {token} = Animation." in design_tokens
            and ".repeatForever(" in design_tokens
        )

    for path in checked_paths:
        source = path.read_text()
        assert "@Environment(\\.accessibilityReduceMotion) private var reduceMotion" in source
        assert "AppMotion." in source
        assert "reduceMotion ? nil" in source or "guard !reduceMotion else { return }" in source
        assert ".onChange(of: reduceMotion)" in source

    assert "Chat loading, recording, and activity pulses respect Reduce Motion" in chat_docs
    assert (
        "breathing pulses remain local presentation effects with reduce-motion handling"
        in onboarding_docs
    )


def test_onboarding_loading_reveal_respects_reduce_motion_and_motion_tokens() -> None:
    loading_source = (VIEWS_ROOT / "Onboarding/OnboardingLoadingStep.swift").read_text()
    onboarding_docs = (REPO_ROOT / "docs/codebase/client/82-views-onboarding.md").read_text()

    assert "laneEntranceAnimation(index: index)" in loading_source
    assert "AppMotion.respectingReduceMotion(" in loading_source
    assert "AppMotion.emphasized.delay(Double(index) * 0.06)" in loading_source
    assert "loading-step reveal" in onboarding_docs


def test_lane_status_progress_is_solid_and_activity_respects_reduce_motion() -> None:
    source = (VIEWS_ROOT / "Shared/LaneStatusRow.swift").read_text()
    shared_docs = (REPO_ROOT / "docs/codebase/client/84-views-shared.md").read_text()

    assert "@Environment(\\.accessibilityReduceMotion) private var reduceMotion" in source
    assert "AppMotion.respectingReduceMotion(reduceMotion, AppMotion.panel)" in source
    assert ".onChange(of: reduceMotion)" in source
    assert "guard !reduceMotion else" in source
    assert ".fill(Color.statusProcessing.opacity(0.9))" in source
    assert "LinearGradient" not in source
    assert "shimmerPhase" not in source
    assert "LaneStatusRow" in shared_docs
    assert "Reduce Motion" in shared_docs


def test_decorative_symbol_effects_respect_reduce_motion() -> None:
    detail_action_bar = (VIEWS_ROOT / "Components/DetailActionBar.swift").read_text()
    tweet_suggestions = (VIEWS_ROOT / "Components/TweetSuggestionsSheet.swift").read_text()
    components_docs = (REPO_ROOT / "docs/codebase/client/81-views-components.md").read_text()

    assert (
        "@Environment(\\.accessibilityReduceMotion) private var reduceMotion" in detail_action_bar
    )
    assert (
        "@Environment(\\.accessibilityReduceMotion) private var reduceMotion" in tweet_suggestions
    )
    assert "if reduceMotion" in detail_action_bar
    assert ".symbolEffect(.bounce, value: learningDeckHintBounce)" in detail_action_bar
    assert (
        ".symbolEffect(.pulse, options: .repeating, isActive: !reduceMotion)" in detail_action_bar
    )
    assert (
        ".symbolEffect(.pulse, isActive: viewModel.isRecording && !reduceMotion)"
        in tweet_suggestions
    )
    assert "decorative symbol effects" in components_docs
    assert "Reduce Motion" in components_docs


def test_landing_title_animation_respects_reduce_motion() -> None:
    source = (VIEWS_ROOT / "LandingView.swift").read_text()
    views_docs = (REPO_ROOT / "docs/codebase/client/80-views.md").read_text()

    assert "@Environment(\\.accessibilityReduceMotion) private var reduceMotion" in source
    assert "if reduceMotion" in source
    assert "staticTitleSection" in source
    assert "animatedTitleSection" in source
    assert "TimelineView(.animation(minimumInterval: 1.0 / 30.0))" in source
    assert "titleContent(yOffset: 0, glowColor: .onboardingAmbientPrimary)" in source
    assert "LandingView" in views_docs
    assert "Reduce Motion" in views_docs
