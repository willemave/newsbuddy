from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


DELETED_IOS_PATHS = [
    "client/newsly/newsly/Views/Components/QuickMicOverlay.swift",
    "client/newsly/newsly/Views/Components/QuickMicContext.swift",
    "client/newsly/newsly/ViewModels/QuickMicViewModel.swift",
    "client/newsly/newsly/Views/Components/CardStackView.swift",
    "client/newsly/newsly/Views/Components/SwipeableCard.swift",
    "client/newsly/newsly/Views/Components/PlaceholderCard.swift",
    "client/newsly/newsly/Views/Components/LongFormCardStackView.swift",
    "client/newsly/newsly/Views/Components/ArticleCardView.swift",
    "client/newsly/newsly/Views/Components/PagedCardView.swift",
    "client/newsly/newsly/ViewModels/CardStackKeyPointsLoader.swift",
    "client/newsly/newsly/Views/Onboarding/AncientScrollRevealView.swift",
    "client/newsly/newsly/Views/Onboarding/RevealPhysicsScene.swift",
    "client/newsly/newslyTests/AncientScrollRevealProgressTests.swift",
    "client/newsly/newsly/Views/KnowledgeDiscoveryView.swift",
    "client/newsly/newsly/ViewModels/DiscoveryViewModel.swift",
    "client/newsly/newsly/Services/DiscoveryService.swift",
    "client/newsly/newsly/Views/Components/DiscoveryStateViews.swift",
    "client/newsly/newsly/Models/DiscoverySuggestion.swift",
    "client/newsly/newsly/ViewModels/ArticleDetailViewModel.swift",
    "client/newsly/newsly/ViewModels/PodcastDetailViewModel.swift",
    "client/newsly/newsly/Views/AuthenticationView.swift",
    "client/newsly/newsly/Views/ContentListView.swift",
    "client/newsly/newsly/Views/Components/FilterBar.swift",
    "client/newsly/newsly/Views/Components/ContentTypeBadge.swift",
    "client/newsly/newsly/Views/Components/DownloadMoreMenu.swift",
    "client/newsly/newsly/Views/Components/SelectableText.swift",
    "client/newsly/newsly/Services/VoiceFFTAnalyzer.swift",
    "client/newsly/newsly/Services/ChatGPTDeepLinkService.swift",
    "client/newsly/newsly/Info.plist.backup",
    "client/newsly/newsly/Models/NewsGroup.swift",
    "client/newsly/newsly/ViewModels/NewsGroupViewModel.swift",
    "client/newsly/newsly/Views/Components/NewsGroupCard.swift",
    "client/newsly/newsly/Views/Components/LearningDeckListSheet.swift",
    "client/newsly/newsly/Views/Components/LearningDeckRow.swift",
    "client/newsly/newsly/Views/Components/LearningDeckRowSupport.swift",
    "client/newsly/newsly/NavigationRestorationModel.swift",
    "client/newsly/newsly/RootTab+Availability.swift",
    "client/newsly/newsly/RootTabSelectionModel.swift",
    "client/newsly/newsly/Shared/TabActivationTiming.swift",
    "client/newsly/newsly/ViewModels/ContentFeedSupport.swift",
    "client/newsly/newsly/ViewModels/LongContentListViewModel.swift",
    "client/newsly/newsly/ViewModels/ShortNewsListViewModel.swift",
    "client/newsly/newsly/ViewModels/CustomNarrationCreationViewModel.swift",
    "client/newsly/newsly/Views/LongFormView.swift",
    "client/newsly/newsly/Views/ShortFormView.swift",
    "client/newsly/newsly/Views/LongFormActionsView.swift",
    "client/newsly/newsly/Views/LongFormAudioController.swift",
    "client/newsly/newsly/Views/LongFormBootstrapStateView.swift",
    "client/newsly/newsly/Views/ShortFormRows.swift",
    "client/newsly/newsly/Views/ShortFormSetupEmptyState.swift",
    "client/newsly/newsly/Views/ShortNewsQuickActionsSection.swift",
    "client/newsly/newsly/Views/ShortNewsScrollReadTracker.swift",
    "client/newsly/newsly/Views/Components/LongFormCard.swift",
    "client/newsly/newsly/Views/Components/FeedActionChip.swift",
    "client/newsly/newsly/Views/Components/FeedListText.swift",
    "client/newsly/newsly/Views/CustomNarrationListSheet.swift",
    "client/newsly/newsly/Views/CustomNarrationPickerSheet.swift",
    "client/newsly/newslyTests/CustomNarrationCreationViewModelTests.swift",
]

DELETED_FONT_PATHS = [
    "client/newsly/newsly/Fonts/Inter.ttf",
    "client/newsly/newsly/Fonts/Inter-Italic.ttf",
    "client/newsly/newsly/Fonts/Roboto.ttf",
    "client/newsly/newsly/Fonts/Roboto-Italic.ttf",
    "client/newsly/ShareExtension/Fonts/Inter.ttf",
    "client/newsly/ShareExtension/Fonts/Inter-Italic.ttf",
    "client/newsly/ShareExtension/Fonts/Roboto.ttf",
    "client/newsly/ShareExtension/Fonts/Roboto-Italic.ttf",
]


def test_phase1_deleted_ios_dead_code_files_stay_removed() -> None:
    offenders = [
        relative_path
        for relative_path in [*DELETED_IOS_PATHS, *DELETED_FONT_PATHS]
        if (REPO_ROOT / relative_path).exists()
    ]

    assert offenders == []


def test_active_client_docs_do_not_reference_deleted_ios_files() -> None:
    deleted_filenames = {
        Path(relative_path).name for relative_path in [*DELETED_IOS_PATHS, *DELETED_FONT_PATHS]
    }
    offenders: list[str] = []

    for path in sorted((REPO_ROOT / "docs/codebase/client").glob("*.md")):
        source = path.read_text()
        for filename in sorted(deleted_filenames):
            if filename in source:
                offenders.append(f"{path.relative_to(REPO_ROOT)} references {filename}")

    assert offenders == []


def test_phase2_deleted_ios_symbols_stay_removed() -> None:
    content_list_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/ContentListViewModel.swift"
    ).read_text()
    chat_service_source = (
        REPO_ROOT / "client/newsly/newsly/Services/ChatService.swift"
    ).read_text()
    cached_image_source = (
        REPO_ROOT / "client/newsly/newsly/Views/Components/CachedAsyncImage.swift"
    ).read_text()

    for deleted_symbol in [
        "case content",
        "func loadContent() async",
        "func markAllAsRead() async",
        "selectedContentTypes",
        "selectedReadFilter",
    ]:
        assert deleted_symbol not in content_list_source

    assert "func waitForMessageCompletion(" not in chat_service_source
    assert "func sendMessage(\n" not in chat_service_source
    assert "@State private var isLoading" not in cached_image_source


def test_briefing_is_the_only_ios_reading_composition_root() -> None:
    app_root = REPO_ROOT / "client/newsly/newsly"
    content_view_source = (app_root / "ContentView.swift").read_text()
    tabs_source = (app_root / "Views/RootTabs.swift").read_text()
    coordinator_source = (app_root / "ViewModels/TabCoordinatorViewModel.swift").read_text()
    settings_source = (app_root / "Services/AppSettings.swift").read_text()
    user_source = (app_root / "Models/User.swift").read_text()
    generated_contracts = (app_root / "Models/Generated/APIContracts.generated.swift").read_text()

    assert "BriefingTab(" in content_view_source
    assert "LongFormTab(" not in content_view_source
    assert "ShortFormTab(" not in content_view_source
    assert "readingExperience" not in content_view_source
    assert "RootTabSelectionModel" not in content_view_source
    assert "NavigationRestorationModel" not in content_view_source
    assert "case longContent" not in coordinator_source
    assert "case shortNews" not in coordinator_source
    assert "case more" not in coordinator_source
    assert "LongFormTab" not in tabs_source
    assert "ShortFormTab" not in tabs_source
    assert "MoreTab" not in tabs_source
    assert "ReadingExperiencePolicy" not in settings_source
    assert "readingExperienceRaw" not in settings_source
    assert "var readingExperience" not in settings_source

    # The server contract remains decodable while presentation no longer branches on it.
    assert "let readingExperience: ReadingExperience" in user_source
    assert 'case classic = "classic"' in generated_contracts

    e2e_root = REPO_ROOT / "tests/ios_e2e/flows"
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in sorted(e2e_root.glob("*.yaml"))
        if "newslyE2EReadingExperience" in path.read_text()
    ]
    assert offenders == []
