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
        APP_ROOT / "Views/Briefing/BriefingView.swift",
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

    docs = (REPO_ROOT / "docs/codebase/client/70-view-models.md").read_text()
    guidelines = (REPO_ROOT / "docs/coding-guidelines-ios.md").read_text()

    assert offenders == []
    assert "Briefing remains on the legacy `ObservableObject` path" in docs
    assert "Use `@Observable` for new view models and UI-facing stores" in guidelines


def test_short_news_scroll_read_batching_uses_async_debounce() -> None:
    source = (VIEW_MODELS_ROOT / "ShortNewsListViewModel.swift").read_text()
    task_bag_source = (VIEW_MODELS_ROOT / "TaskBag.swift").read_text()
    docs = (REPO_ROOT / "docs/codebase/client/70-view-models.md").read_text()

    assert "private let scrollReadDebounceNanoseconds: UInt64 = 300_000_000" in source
    assert "private let tasks = TaskBag<TaskKey>()" in source
    assert "pendingScrollReadIds.formUnion(ids)" in source
    assert "tasks.runReplacing(.scrollRead)" in source
    assert "try await Task.sleep(nanoseconds: scrollReadDebounceNanoseconds)" in source
    assert "scrollReadTask" not in source
    assert "PassthroughSubject" not in source
    assert "AnyCancellable" not in source
    assert ".collect(.byTime" not in source
    assert "deinit {\n        cancelAll()\n    }" in task_bag_source
    assert "scroll-read batching uses an async debounce task" in docs


def test_learning_decks_polling_uses_task_bag() -> None:
    source = (VIEW_MODELS_ROOT / "LearningDecksViewModel.swift").read_text()
    docs = (REPO_ROOT / "docs/codebase/client/70-view-models.md").read_text()

    assert "case deckPolling(Int)" in source
    assert "private let tasks = TaskBag<LearningDecksTaskKey>()" in source
    assert "tasks.runIfIdle(taskKey)" in source
    assert "tasks.cancel(.deckPolling(deck.id))" in source
    assert "Task { [weak self]" not in source
    assert "pollingDeckIDs" not in source
    assert "Learning Deck polling uses `TaskBag`" in docs


def test_content_feed_repositories_expose_async_methods_directly() -> None:
    support_source = (VIEW_MODELS_ROOT / "ContentFeedSupport.swift").read_text()
    content_repository_source = (APP_ROOT / "Repositories/ContentRepository.swift").read_text()
    read_status_source = (APP_ROOT / "Repositories/ReadStatusRepository.swift").read_text()
    api_source = (APP_ROOT / "Services/APIClient.swift").read_text()

    assert "firstValue" not in support_source
    assert ") async throws -> ContentListResponse" in content_repository_source
    assert "func loadDetail(id: Int) async throws -> ContentDetail" in content_repository_source
    assert "func markRead(ids: [Int]) async throws" in read_status_source
    assert "client.publisher(" not in content_repository_source
    assert "client.publisher(" not in read_status_source
    assert "func publisher<" not in api_source
    assert "publisherVoid" not in api_source


def test_voice_dictation_view_models_use_event_coordinator() -> None:
    speech_source = (APP_ROOT / "Services/SpeechTranscribing.swift").read_text()
    coordinator_source = (VIEW_MODELS_ROOT / "VoiceDictationCoordinator.swift").read_text()
    docs = (REPO_ROOT / "docs/codebase/client/70-view-models.md").read_text()

    assert "func events() -> AsyncStream<SpeechTranscriptionEvent>" in speech_source
    assert "final class VoiceDictationCoordinator" in coordinator_source
    assert "for await event in events" in coordinator_source

    expected_coordinator_users = [
        VIEW_MODELS_ROOT / "ChatSessionViewModel.swift",
        VIEW_MODELS_ROOT / "KnowledgeHubViewModel.swift",
        VIEW_MODELS_ROOT / "OnboardingViewModel.swift",
        VIEW_MODELS_ROOT / "DiscoveryPersonalizeViewModel.swift",
        VIEW_MODELS_ROOT / "LearningDeckFocusRecorder.swift",
    ]
    for path in expected_coordinator_users:
        assert "VoiceDictationCoordinator" in path.read_text(), path

    direct_callback_tokens = [
        ".onTranscriptFinal =",
        ".onTranscriptDelta =",
        ".onError =",
        ".onStateChange =",
        ".onStopReason =",
    ]
    for path in sorted(VIEW_MODELS_ROOT.rglob("*.swift")):
        source = path.read_text()
        for token in direct_callback_tokens:
            assert token not in source, f"{path.relative_to(REPO_ROOT)} directly assigns {token}"

    assert "voice dictation events are consumed through `VoiceDictationCoordinator`" in docs
