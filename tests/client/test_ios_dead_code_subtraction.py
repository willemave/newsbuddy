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
        Path(relative_path).name
        for relative_path in [*DELETED_IOS_PATHS, *DELETED_FONT_PATHS]
    }
    offenders: list[str] = []

    for path in sorted((REPO_ROOT / "docs/codebase/client").glob("*.md")):
        source = path.read_text()
        for filename in sorted(deleted_filenames):
            if filename in source:
                offenders.append(f"{path.relative_to(REPO_ROOT)} references {filename}")

    assert offenders == []
