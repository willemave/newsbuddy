from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "client/newsly/newsly"
VIEW_MODELS_ROOT = REPO_ROOT / "client/newsly/newsly/ViewModels"


def test_app_sources_do_not_use_combine_publisher_bridges() -> None:
    forbidden_tokens = [
        "import Combine",
        "AnyPublisher",
        "AnyCancellable",
        "PassthroughSubject",
        "CurrentValueSubject",
        ".eraseToAnyPublisher",
        "func publisher<",
        "publisherVoid",
    ]

    for path in sorted(APP_ROOT.rglob("*.swift")):
        source = path.read_text()
        for token in forbidden_tokens:
            assert token not in source, f"{path.relative_to(REPO_ROOT)} still contains {token}"


def test_legacy_observation_wrappers_are_limited_to_briefing_surface() -> None:
    allowed_legacy_paths = {
        APP_ROOT / "ViewModels/BriefingViewModel.swift",
        APP_ROOT / "ViewModels/BriefingDigViewModel.swift",
        *sorted((APP_ROOT / "Views/Briefing").glob("*.swift")),
    }
    legacy_tokens = [
        "ObservableObject",
        "@Published",
        "@StateObject",
        "@ObservedObject",
        "@EnvironmentObject",
    ]
    offenders: list[str] = []

    for path in sorted(APP_ROOT.rglob("*.swift")):
        if path in allowed_legacy_paths:
            continue

        source = path.read_text()
        for token in legacy_tokens:
            if token in source:
                offenders.append(f"{path.relative_to(REPO_ROOT)} still contains {token}")

    guidelines = (REPO_ROOT / "docs/coding-guidelines-ios.md").read_text()

    assert offenders == []
    assert "Use `@Observable` for new view models and UI-facing stores" in guidelines


def test_learning_decks_polling_uses_task_bag() -> None:
    source = (VIEW_MODELS_ROOT / "LearningDecksViewModel.swift").read_text()

    assert "case deckPolling(Int)" in source
    assert "private let tasks = TaskBag<LearningDecksTaskKey>()" in source
    assert "tasks.runIfIdle(taskKey)" in source
    assert "tasks.cancel(.deckPolling(deck.id))" in source
    assert "Task { [weak self]" not in source
    assert "pollingDeckIDs" not in source


def test_read_status_repository_exposes_async_methods_directly() -> None:
    read_status_source = (APP_ROOT / "Repositories/ReadStatusRepository.swift").read_text()
    api_source = (APP_ROOT / "Services/APIClient.swift").read_text()

    assert "func markRead(ids: [Int]) async throws" in read_status_source
    assert "client.publisher(" not in read_status_source
    assert "func publisher<" not in api_source
    assert "publisherVoid" not in api_source


def test_voice_dictation_view_models_use_event_coordinator() -> None:
    chat_session_source = (VIEW_MODELS_ROOT / "ChatSessionViewModel.swift").read_text()

    expected_coordinator_users = [
        VIEW_MODELS_ROOT / "ChatVoiceInputController.swift",
        VIEW_MODELS_ROOT / "LearningHubViewModel.swift",
        VIEW_MODELS_ROOT / "OnboardingViewModel.swift",
        VIEW_MODELS_ROOT / "LearningDeckFocusRecorder.swift",
        VIEW_MODELS_ROOT / "TweetSuggestionsViewModel.swift",
    ]
    for path in expected_coordinator_users:
        assert "VoiceDictationCoordinator" in path.read_text(), path

    assert "ChatVoiceInputController" in chat_session_source

    direct_callback_tokens = [
        ".onTranscriptFinal =",
        ".onTranscriptDelta =",
        ".onError =",
        ".onStateChange =",
        ".onStopReason =",
    ]
    for path in sorted(VIEW_MODELS_ROOT.rglob("*.swift")):
        if path.name == "VoiceDictationCoordinator.swift":
            continue
        source = path.read_text()
        for token in direct_callback_tokens:
            assert token not in source, f"{path.relative_to(REPO_ROOT)} directly assigns {token}"


def test_chat_route_queue_is_acknowledged_only_after_root_presentation() -> None:
    content_source = (APP_ROOT / "ContentView.swift").read_text()
    coordinator_source = (APP_ROOT / "Services/ChatNavigationCoordinator.swift").read_text()
    root_tabs_source = (APP_ROOT / "Views/RootTabs.swift").read_text()

    assert "pendingChatRouteAfterMoreDismiss" not in content_source
    assert content_source.count("drainPendingChatRoute()") >= 3
    assert "func acknowledgePresented(_ route: ChatSessionRoute)" in coordinator_source
    drain_start = content_source.index("private func drainPendingChatRoute()")
    presentation = content_source.index("presentChatSession(route)", drain_start)
    begin = content_source.index("chatNavigation.beginPresentation(", drain_start)
    assert begin < presentation
    assert "let presentedRoute = chatNavigation.presentedRoute" in content_source
    assert "chatNavigation.acknowledgePresented(presentedRoute)" in content_source
    learning_call = content_source[
        content_source.index("LearningTab(") : content_source.index(
            ".environment(", content_source.index("LearningTab(")
        )
    ]
    learning_tab = root_tabs_source[
        root_tabs_source.index("struct LearningTab") : root_tabs_source.index(
            "struct BriefingCompactTabBarInset"
        )
    ]
    assert "onSelectSession: openChatSession" in learning_call
    assert "let onSelectSession: (ChatSessionRoute) -> Void" in learning_tab
    assert "onSelectSession: onSelectSession" in learning_tab
    assert "private func pushSession(_ route: ChatSessionRoute)" not in root_tabs_source
    learning_path_change = content_source[
        content_source.index(".onChange(of: learningPath.count)") : content_source.index(
            ".onChange(of: scenePhase)"
        )
    ]
    assert "if oldValue > 0, newValue == 0" in learning_path_change
    assert learning_path_change.count("drainPendingChatRoute()") == 1
    assert learning_path_change.index("drainPendingChatRoute()") > learning_path_change.index(
        "chatNavigation.acknowledgePresented(presentedRoute)"
    )


def test_removed_discovery_personalization_surface_stays_deleted() -> None:
    assert not (VIEW_MODELS_ROOT / "DiscoveryPersonalizeViewModel.swift").exists()
    assert not (APP_ROOT / "Views/DiscoveryPersonalizeSheet.swift").exists()
