from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "client/newsly/newsly/Services"


def test_network_services_use_timeout_session_and_single_auth_decoder_setup() -> None:
    api_client_source = (SERVICES_ROOT / "APIClient.swift").read_text()
    auth_source = (SERVICES_ROOT / "AuthenticationService.swift").read_text()
    services_docs = (REPO_ROOT / "docs/codebase/client/50-services.md").read_text()

    assert "static let newslyDefault: URLSession" in api_client_source
    assert "configuration.timeoutIntervalForRequest = 30" in api_client_source
    assert "configuration.timeoutIntervalForResource = 60" in api_client_source

    for service_path in SERVICES_ROOT.glob("*.swift"):
        assert "URLSession.shared" not in service_path.read_text(), service_path

    assert "private enum AuthenticationResponseDecoder" in auth_source
    assert auth_source.count("JSONDecoder()") == 1
    assert auth_source.count("dateDecodingStrategy = .iso8601") == 1
    assert "single ISO-8601 auth response decoder factory" in services_docs


def test_auth_refresh_is_split_from_keychain_storage() -> None:
    keychain_source = (SERVICES_ROOT / "KeychainManager.swift").read_text()
    auth_error_source = (SERVICES_ROOT / "AuthError.swift").read_text()
    token_refresh_source = (SERVICES_ROOT / "TokenRefreshService.swift").read_text()
    openai_source = (SERVICES_ROOT / "OpenAIService.swift").read_text()
    speech_source = (SERVICES_ROOT / "SpeechTranscribing.swift").read_text()
    auth_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/AuthenticationViewModel.swift"
    ).read_text()
    chat_sessions_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/ChatSessionsViewModel.swift"
    ).read_text()
    content_list_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/ContentListViewModel.swift"
    ).read_text()
    content_detail_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/ContentDetailViewModel.swift"
    ).read_text()
    long_content_list_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/LongContentListViewModel.swift"
    ).read_text()
    news_group_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/NewsGroupViewModel.swift"
    ).read_text()
    submission_status_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/SubmissionStatusViewModel.swift"
    ).read_text()
    custom_narration_creation_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/CustomNarrationCreationViewModel.swift"
    ).read_text()
    custom_narration_library_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/CustomNarrationLibraryViewModel.swift"
    ).read_text()
    onboarding_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/OnboardingViewModel.swift"
    ).read_text()
    discovery_personalize_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/DiscoveryPersonalizeViewModel.swift"
    ).read_text()
    learning_decks_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/LearningDecksViewModel.swift"
    ).read_text()
    learning_deck_reader_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/LearningDeckReaderViewModel.swift"
    ).read_text()
    learning_deck_focus_recorder_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/LearningDeckFocusRecorder.swift"
    ).read_text()
    chat_session_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/ChatSessionViewModel.swift"
    ).read_text()
    tweet_suggestions_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/TweetSuggestionsViewModel.swift"
    ).read_text()
    chat_dependencies_source = (
        REPO_ROOT / "client/newsly/newsly/App/ChatDependencies.swift"
    ).read_text()
    link_submission_coordinator_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/LinkSubmissionCoordinator.swift"
    ).read_text()
    detail_chat_coordinator_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/DetailChatCoordinator.swift"
    ).read_text()
    discussion_sheet_coordinator_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/DiscussionSheetCoordinator.swift"
    ).read_text()
    podcast_audio_controller_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/PodcastAudioController.swift"
    ).read_text()
    scraper_settings_view_model_source = (
        REPO_ROOT / "client/newsly/newsly/ViewModels/ScraperSettingsViewModel.swift"
    ).read_text()
    content_detail_view_source = (
        REPO_ROOT / "client/newsly/newsly/Views/ContentDetailView.swift"
    ).read_text()
    recently_read_view_source = (
        REPO_ROOT / "client/newsly/newsly/Views/RecentlyReadView.swift"
    ).read_text()
    app_chrome_source = (REPO_ROOT / "client/newsly/newsly/Shared/AppChrome.swift").read_text()
    badge_stats_source = (SERVICES_ROOT / "BadgeStatsRefreshCoordinator.swift").read_text()
    services_docs = (REPO_ROOT / "docs/codebase/client/50-services.md").read_text()
    view_model_docs = (REPO_ROOT / "docs/codebase/client/70-view-models.md").read_text()

    assert "final class KeychainManager" in keychain_source
    assert "protocol AuthTokenStore" in keychain_source
    assert "private let accessGroupLock = NSLock()" in keychain_source
    assert "private var accessGroup: String?" in keychain_source
    assert "func configure(accessGroup: String?)" in keychain_source
    assert "private func currentAccessGroup() -> String?" in keychain_source
    assert keychain_source.count("accessGroupLock.lock()") == 2
    assert keychain_source.count("defer { accessGroupLock.unlock() }") == 2
    assert "let configuredAccessGroup = currentAccessGroup()" in keychain_source
    assert "if let accessGroup = currentAccessGroup()" in keychain_source
    assert "enum AuthError" not in keychain_source
    assert "final class TokenRefreshService" not in keychain_source
    assert "actor RefreshCoordinator" not in keychain_source

    assert "enum AuthError" in auth_error_source
    assert "var userFacingMessage" in auth_error_source

    assert "protocol TokenRefreshing" in token_refresh_source
    assert "func accessToken() async throws -> String" in token_refresh_source
    assert "final class TokenRefreshService" in token_refresh_source
    assert "private actor RefreshCoordinator" in token_refresh_source

    assert "AuthTokenStore" not in openai_source
    assert "KeychainManager.shared" not in openai_source
    assert "private let tokenRefresher: TokenRefreshing" in openai_source
    assert "tokenRefresher: TokenRefreshing = TokenRefreshService.shared" in openai_source
    assert "tokenRefresher.accessToken()" in openai_source
    assert "tokenRefresher.refreshAccessToken()" in openai_source

    assert "KeychainManager.shared" not in speech_source
    assert "TokenRefreshService.shared.hasStoredCredentialMaterial" in speech_source

    assert "authenticationRequiredObserver" in auth_view_model_source
    assert (
        "NotificationCenter.default.removeObserver(authenticationRequiredObserver)"
        in auth_view_model_source
    )
    assert "forName: .authenticationRequired" in auth_view_model_source
    assert "private let authService: any AuthenticationServicing" in auth_view_model_source
    assert "private let tokenStore: any AuthTokenStore" in auth_view_model_source
    assert "AuthenticationService.shared" not in auth_view_model_source
    assert "KeychainManager.shared" not in auth_view_model_source
    assert "static func makeAuthenticationViewModel()" in app_chrome_source
    assert "AuthenticationService.shared" in app_chrome_source
    assert "KeychainManager.shared" in app_chrome_source
    assert "protocol ChatSessionsServicing" in chat_sessions_view_model_source
    assert "private let chatService: any ChatSessionsServicing" in chat_sessions_view_model_source
    assert "ChatService.shared" not in chat_sessions_view_model_source
    assert "static func makeChatSessionsViewModel()" in app_chrome_source
    assert "ChatService.shared" in app_chrome_source
    assert "protocol ContentSummaryListServicing" in content_list_view_model_source
    assert (
        "private let contentService: any ContentSummaryListServicing"
        in content_list_view_model_source
    )
    assert "private let unreadCountService: UnreadCountService" in content_list_view_model_source
    assert "ContentService.shared" not in content_list_view_model_source
    assert "UnreadCountService.shared" not in content_list_view_model_source
    assert "func markAsUnreadAndRemove(_ contentId: Int) async" in content_list_view_model_source
    assert "ContentService.shared" not in recently_read_view_source
    assert "viewModel.markAsUnreadAndRemove(content.id)" in recently_read_view_source
    assert (
        "private let contentService: any ContentSummaryListServicing"
        in long_content_list_view_model_source
    )
    assert "private let toastPresenter: any ToastPresenting" in long_content_list_view_model_source
    assert "ContentService.shared" not in long_content_list_view_model_source
    assert "ToastService.shared" not in long_content_list_view_model_source
    assert "private let toastPresenter: any ToastPresenting" in news_group_view_model_source
    assert "UnreadCountService.shared" not in news_group_view_model_source
    assert "ToastService.shared" not in news_group_view_model_source
    assert "ContentService.shared" not in submission_status_view_model_source
    assert "protocol CustomNarrationAudioServicing" in custom_narration_creation_view_model_source
    assert "AudioEpisodeService.shared" not in custom_narration_creation_view_model_source
    assert "ToastService.shared" not in custom_narration_creation_view_model_source
    assert "AudioEpisodeService.shared" not in custom_narration_library_view_model_source
    assert "NarrationPlaybackService.shared" not in custom_narration_library_view_model_source
    assert "UnreadCountService.shared" not in custom_narration_library_view_model_source
    assert "ToastService.shared" not in custom_narration_library_view_model_source
    assert "OnboardingService.shared" not in onboarding_view_model_source
    assert "OnboardingStateStore.shared" not in onboarding_view_model_source
    assert "OnboardingService.shared" not in discovery_personalize_view_model_source
    assert "OnboardingStateStore.shared" not in discovery_personalize_view_model_source
    assert "LearningDeckService.shared" not in learning_decks_view_model_source
    assert "ChatService.shared" not in learning_deck_reader_view_model_source
    assert "LearningDeckService.shared" not in learning_deck_reader_view_model_source
    assert "OpenAIService.shared" not in learning_deck_focus_recorder_source
    assert "AuthenticationService.shared" not in chat_session_view_model_source
    assert "OpenAIService.shared" not in chat_session_view_model_source
    assert "AppSettings.shared" not in chat_session_view_model_source
    assert "KeychainManager.shared" not in chat_session_view_model_source
    assert "ContentService.shared" not in tweet_suggestions_view_model_source
    assert "TwitterShareService.shared" not in tweet_suggestions_view_model_source
    assert "VoiceDictationService.shared" not in tweet_suggestions_view_model_source
    assert "AuthenticationService.shared" not in tweet_suggestions_view_model_source
    assert "OpenAIService.shared" not in tweet_suggestions_view_model_source
    assert "AppSettings.shared" not in tweet_suggestions_view_model_source
    assert "KeychainManager.shared" not in tweet_suggestions_view_model_source
    assert "refreshTranscriptionAvailability" in chat_dependencies_source
    assert "setBackendTranscriptionAvailable" in chat_dependencies_source
    assert "protocol ContentDetailServicing" in content_detail_view_model_source
    assert "protocol DetectedFeedSubscribing" in content_detail_view_model_source
    assert (
        "private let contentService: any ContentDetailServicing" in content_detail_view_model_source
    )
    assert (
        "private let feedSubscriptionService: any DetectedFeedSubscribing"
        in content_detail_view_model_source
    )
    assert "private let toastPresenter: any ToastPresenting" in content_detail_view_model_source
    assert "ContentService.shared" not in content_detail_view_model_source
    assert "ScraperConfigService.shared" not in content_detail_view_model_source
    assert "ToastService.shared" not in content_detail_view_model_source
    assert "ToastService.shared" not in link_submission_coordinator_source
    assert "ChatService.shared" not in detail_chat_coordinator_source
    assert "ChatNavigationCoordinator.shared" not in detail_chat_coordinator_source
    assert "ToastService.shared" not in detail_chat_coordinator_source
    assert "ContentService.shared" not in discussion_sheet_coordinator_source
    assert "AudioEpisodeService.shared" not in podcast_audio_controller_source
    assert "NarrationPlaybackService.shared" not in podcast_audio_controller_source
    assert "ChatService.shared" not in content_detail_view_source
    assert "protocol DetailChatServicing" in detail_chat_coordinator_source
    assert "protocol ContentDiscussionServicing" in discussion_sheet_coordinator_source
    assert "protocol PodcastAudioEpisodeServicing" in podcast_audio_controller_source
    assert "static func makeContentDetailViewModel(" in app_chrome_source
    assert "static func makeDetailChatCoordinator()" in app_chrome_source
    assert "static func makeDiscussionSheetCoordinator()" in app_chrome_source
    assert "static func makePodcastAudioController()" in app_chrome_source
    assert "RootDependencyFactory.makeContentDetailViewModel(" in content_detail_view_source
    assert "RootDependencyFactory.makeDetailChatCoordinator()" in content_detail_view_source
    assert "RootDependencyFactory.makeDiscussionSheetCoordinator()" in content_detail_view_source
    assert "RootDependencyFactory.makePodcastAudioController()" in content_detail_view_source
    assert "protocol ScraperSettingsServicing" in scraper_settings_view_model_source
    assert "private let service: any ScraperSettingsServicing" in scraper_settings_view_model_source
    assert "ScraperConfigService.shared" not in scraper_settings_view_model_source
    assert "static func makeScraperSettingsViewModel(" in app_chrome_source
    assert "service: ScraperConfigService.shared" in app_chrome_source
    assert "contentService: ContentService.shared" in app_chrome_source
    assert "static func makeContentListViewModel(" in app_chrome_source
    assert "static func makeCustomNarrationCreationViewModel()" in app_chrome_source
    assert "static func makeCustomNarrationLibraryViewModel(" in app_chrome_source
    assert "static func makeSubmissionStatusViewModel(" in app_chrome_source
    assert "static func makeOnboardingViewModel(user: User)" in app_chrome_source
    assert "static func makeDiscoveryPersonalizeViewModel(userId: Int)" in app_chrome_source
    assert "static func makeLearningDecksViewModel()" in app_chrome_source
    assert "static func makeLearningDeckReaderViewModel(" in app_chrome_source
    assert "static func makeLearningDeckFocusRecorder()" in app_chrome_source
    assert "static func makeTweetSuggestionsViewModel()" in app_chrome_source
    assert ".authenticationRequired" not in badge_stats_source

    assert "`AuthError.swift`" in services_docs
    assert "`TokenRefreshService.swift`" in services_docs
    assert "locked token access-group storage" in services_docs
    assert "AuthenticationViewModel` is its direct observer" in services_docs
    assert "AuthenticationViewModel` takes auth and token-store dependencies" in view_model_docs
    assert (
        "ContentListViewModel` and `LongContentListViewModel` take their content-list service"
        in view_model_docs
    )
    assert (
        "ContentDetailViewModel` takes content, detected-feed, and toast dependencies"
        in view_model_docs
    )
    assert (
        "detail-local coordinators take chat, discussion, audio, navigation, and toast dependencies"
        in view_model_docs
    )
    assert "custom narration and submission-status view models are factory-wired" in view_model_docs
    assert (
        "onboarding, discovery personalization, and Learning Deck view models are factory-wired"
        in view_model_docs
    )
    assert (
        "ChatSessionViewModel and TweetSuggestionsViewModel receive auth, token, "
        "and transcription availability dependencies"
        in view_model_docs
    )
    assert "ScraperSettingsViewModel` takes its source-settings service" in view_model_docs
    assert "chat history and auth are factory-injected" in view_model_docs


def test_view_models_do_not_read_service_singletons_directly() -> None:
    view_models_root = REPO_ROOT / "client/newsly/newsly/ViewModels"
    allowed_shared_access = {
        view_models_root / "ActivityViewPresenter.swift": ["UIApplication.shared"],
    }
    offenders: list[str] = []

    for path in sorted(view_models_root.rglob("*.swift")):
        allowed_tokens = allowed_shared_access.get(path, [])
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if ".shared" not in line:
                continue
            if any(token in line for token in allowed_tokens):
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    guidelines = (REPO_ROOT / "docs/coding-guidelines-ios.md").read_text()
    view_model_docs = (REPO_ROOT / "docs/codebase/client/70-view-models.md").read_text()

    assert offenders == []
    assert "Prefer injected dependencies in `init` over hidden singleton lookups" in guidelines
    assert "live singleton wiring lives in `RootDependencyFactory`" in view_model_docs


def test_image_cache_has_periodic_cleanup_and_error_logging() -> None:
    image_cache_source = (SERVICES_ROOT / "ImageCacheService.swift").read_text()
    services_docs = (REPO_ROOT / "docs/codebase/client/50-services.md").read_text()

    assert "private let imageCacheLogger = Logger(" in image_cache_source
    assert "diskCleanupInterval" in image_cache_source
    assert "lastDiskCleanupDate" in image_cache_source
    assert "diskCleanupTask" in image_cache_source
    assert 'scheduleDiskCleanupIfNeeded(reason: "init", force: true)' in image_cache_source
    assert 'scheduleDiskCleanupIfNeeded(reason: "write")' in image_cache_source
    assert "catch {}" not in image_cache_source
    assert "Failed to enumerate image cache during clear" in image_cache_source
    assert "Failed to write image cache data" in image_cache_source
    assert "Failed to download image" in image_cache_source
    assert "Failed to prefetch image" in image_cache_source
    assert "throttled disk cleanup" in services_docs


def test_active_chat_polling_is_lifecycle_gated() -> None:
    active_chat_source = (SERVICES_ROOT / "ActiveChatSessionManager.swift").read_text()
    content_view_source = (REPO_ROOT / "client/newsly/newsly/ContentView.swift").read_text()
    active_chat_tests = (
        REPO_ROOT / "client/newsly/newslyTests/ActiveChatSessionManagerTests.swift"
    ).read_text()
    services_docs = (REPO_ROOT / "docs/codebase/client/50-services.md").read_text()

    assert "private var isPollingSuspended = false" in active_chat_source
    assert "func setPollingSuspended(_ isSuspended: Bool)" in active_chat_source
    assert "restartPollingForActiveSessions()" in active_chat_source
    assert "guard startsPolling, !isPollingSuspended" in active_chat_source
    assert "chatSessionManager.setPollingSuspended(scenePhase != .active)" in content_view_source
    assert (
        "testLifecycleSuspensionPausesAndResumesPollingWithoutDroppingTrackedSession"
        in active_chat_tests
    )
    assert "lifecycle-gated active-session polling" in services_docs


def test_badge_stats_refresh_is_scene_phase_gated() -> None:
    coordinator_source = (SERVICES_ROOT / "BadgeStatsRefreshCoordinator.swift").read_text()
    unread_source = (SERVICES_ROOT / "UnreadCountService.swift").read_text()
    processing_source = (SERVICES_ROOT / "ProcessingCountService.swift").read_text()
    content_view_source = (REPO_ROOT / "client/newsly/newsly/ContentView.swift").read_text()
    root_docs = (REPO_ROOT / "docs/codebase/client/20-app-target-root.md").read_text()
    services_docs = (REPO_ROOT / "docs/codebase/client/50-services.md").read_text()

    assert "private var isRefreshSuspended = false" in coordinator_source
    assert "func setRefreshSuspended(_ isSuspended: Bool)" in coordinator_source
    assert "guard !isRefreshSuspended else { return }" in coordinator_source
    assert "guard hasActiveProcessing, !isRefreshSuspended" in coordinator_source

    assert "func setPeriodicRefreshSuspended(_ isSuspended: Bool)" in unread_source
    assert "func setPeriodicRefreshSuspended(_ isSuspended: Bool)" in processing_source
    assert (
        "@State private var processingCountService = ProcessingCountService.shared"
        in content_view_source
    )
    assert (
        "unreadCountService.setPeriodicRefreshSuspended(scenePhase != .active)"
        in content_view_source
    )
    assert (
        "processingCountService.setPeriodicRefreshSuspended(scenePhase != .active)"
        in content_view_source
    )
    assert (
        "unreadCountService.setPeriodicRefreshSuspended(newPhase != .active)" in content_view_source
    )
    assert (
        "processingCountService.setPeriodicRefreshSuspended(newPhase != .active)"
        in content_view_source
    )

    assert "badge stats retries" in root_docs
    assert (
        "`BadgeStatsRefreshCoordinator` retries active processing badge refreshes" in services_docs
    )


def test_e2e_route_injection_is_consolidated_at_root() -> None:
    app_root = REPO_ROOT / "client/newsly/newsly"
    injector_source = (app_root / "E2ERouteInjector.swift").read_text()
    content_view_source = (app_root / "ContentView.swift").read_text()
    root_docs = (REPO_ROOT / "docs/codebase/client/20-app-target-root.md").read_text()

    assert "final class E2ERouteInjector" in injector_source
    assert "private var hasAppliedOpenChatRoute = false" in injector_source
    assert "private var hasAppliedOpenContentRoute = false" in injector_source
    assert "func applyOpenChatRouteIfNeeded" in injector_source
    assert "func applyOpenContentRouteIfNeeded" in injector_source
    assert "E2ETestLaunch.openChatSessionId" in injector_source
    assert "E2ETestLaunch.openContentId" in injector_source
    assert "E2ETestLaunch.openContentType" in injector_source
    assert injector_source.count("Task { @MainActor in") == 2
    assert injector_source.count("await Task.yield()") == 2

    route_launch_offenders: list[str] = []
    for path in sorted(app_root.rglob("*.swift")):
        if path.name == "E2ERouteInjector.swift":
            continue
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if "E2ETestLaunch.open" in line:
                route_launch_offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}"
                )

    assert route_launch_offenders == []
    assert "@State private var e2eRouteInjector = E2ERouteInjector()" in content_view_source
    assert "applyE2ERoutesIfNeeded()" in content_view_source
    assert "if newPhase == .active" in content_view_source
    assert (
        "e2eRouteInjector.applyOpenContentRouteIfNeeded(openContentRoute: openContentRoute)"
        in content_view_source
    )
    assert (
        "e2eRouteInjector.applyOpenChatRouteIfNeeded(openChatSession: openChatSession)"
        in content_view_source
    )
    assert "E2ETestLaunch.open" not in content_view_source
    assert "newslyE2EOpen" not in content_view_source

    assert "only app-shell reader for launch-time E2E content/chat routes" in root_docs


def test_share_extension_uses_shared_brand_style() -> None:
    share_controller_source = (
        REPO_ROOT / "client/newsly/ShareExtension/ShareViewController.swift"
    ).read_text()
    shared_style_source = (
        REPO_ROOT / "client/newsly/newsly/Shared/ShareExtensionStyle.swift"
    ).read_text()
    project_source = (REPO_ROOT / "client/newsly/newsly.xcodeproj/project.pbxproj").read_text()
    share_extension_docs = (REPO_ROOT / "docs/codebase/client/90-share-extension.md").read_text()

    assert "fileprivate extension UIColor" not in share_controller_source
    assert "ShareExtensionTypography" not in share_controller_source
    assert "ShareExtensionStyle.brandAccent" in share_controller_source
    assert "ShareExtensionStyle.font" in share_controller_source
    assert "ShareExtensionStyle.titleFont" in share_controller_source

    assert 'static let brandColorAssetName = "ShareBrandPrimary"' in shared_style_source
    assert "UIColor(named: brandColorAssetName)" in shared_style_source
    assert 'static let bodyFamily = "Lato-Regular"' in shared_style_source
    assert 'static let titleFamily = "Lora-Regular"' in shared_style_source

    assert "Shared/ShareExtensionStyle.swift" in project_source
    assert (
        REPO_ROOT / "client/newsly/newsly/Assets.xcassets/ShareBrandPrimary.colorset/Contents.json"
    ).exists()
    assert (
        REPO_ROOT
        / "client/newsly/ShareExtension/Assets.xcassets/ShareBrandPrimary.colorset/Contents.json"
    ).exists()
    assert "`newsly/Shared/ShareExtensionStyle.swift`" in share_extension_docs
    assert "`Assets.xcassets/ShareBrandPrimary.colorset`" in share_extension_docs


def test_voice_dictation_haptics_live_in_swiftui_mic_button() -> None:
    voice_service_source = (SERVICES_ROOT / "VoiceDictationService.swift").read_text()
    mic_button_source = (
        REPO_ROOT / "client/newsly/newsly/Views/Components/TapToTalkMicButton.swift"
    ).read_text()
    components_docs = (REPO_ROOT / "docs/codebase/client/81-views-components.md").read_text()
    services_docs = (REPO_ROOT / "docs/codebase/client/50-services.md").read_text()

    assert "UIImpactFeedbackGenerator" not in voice_service_source
    assert "UINotificationFeedbackGenerator" not in voice_service_source
    assert "UISelectionFeedbackGenerator" not in voice_service_source
    assert "import UIKit" not in voice_service_source

    assert "let isTranscribing: Bool" in mic_button_source
    assert ".sensoryFeedback(.impact(weight: .light), trigger: isRecording)" in mic_button_source
    assert ".sensoryFeedback(.success, trigger: isTranscribing)" in mic_button_source

    assert "mic haptics are SwiftUI `.sensoryFeedback`" in services_docs
    assert "`TapToTalkMicButton` owns shared voice haptics" in components_docs
