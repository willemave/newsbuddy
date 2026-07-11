//
//  AppChrome.swift
//  newsly
//
//  Created by Assistant on 3/20/26.
//

import SwiftUI
import UIKit

private struct PersistentBottomChromeInsetKey: EnvironmentKey {
    static let defaultValue: CGFloat = 0
}

extension EnvironmentValues {
    var persistentBottomChromeInset: CGFloat {
        get { self[PersistentBottomChromeInsetKey.self] }
        set { self[PersistentBottomChromeInsetKey.self] = newValue }
    }
}

enum AppChrome {
    static func configure() {
        let chromeAccent = UIColor.appChromeAccent
        let unselected = UIColor.tertiaryLabel
        let surface = UIColor.appSurfacePrimary

        let itemAppearance = UITabBarItemAppearance()
        itemAppearance.selected.iconColor = chromeAccent
        itemAppearance.selected.titleTextAttributes = [
            .foregroundColor: chromeAccent,
            .font: UIFont.appSans(size: 10, weight: .medium)
        ]
        itemAppearance.normal.iconColor = unselected
        itemAppearance.normal.titleTextAttributes = [
            .foregroundColor: unselected,
            .font: UIFont.appSans(size: 10, weight: .medium)
        ]

        let tabAppearance = UITabBarAppearance()
        tabAppearance.configureWithTransparentBackground()
        tabAppearance.backgroundColor = surface.withAlphaComponent(0.92)
        tabAppearance.shadowColor = UIColor.separator
        tabAppearance.stackedLayoutAppearance = itemAppearance
        tabAppearance.inlineLayoutAppearance = itemAppearance
        tabAppearance.compactInlineLayoutAppearance = itemAppearance
        UITabBar.appearance().standardAppearance = tabAppearance
        UITabBar.appearance().scrollEdgeAppearance = tabAppearance
        UITabBar.appearance().tintColor = chromeAccent

        let navigationAppearance = UINavigationBarAppearance()
        navigationAppearance.configureWithTransparentBackground()
        navigationAppearance.backgroundColor = surface.withAlphaComponent(0.92)
        navigationAppearance.shadowColor = UIColor.separator
        navigationAppearance.titleTextAttributes = [
            .foregroundColor: UIColor.appOnSurface,
            .font: UIFont.appSerif(size: 17, weight: .semibold)
        ]
        navigationAppearance.largeTitleTextAttributes = [
            .foregroundColor: UIColor.appOnSurface,
            .font: UIFont.appSerif(size: 34, weight: .semibold)
        ]
        UINavigationBar.appearance().standardAppearance = navigationAppearance
        UINavigationBar.appearance().scrollEdgeAppearance = navigationAppearance
        UINavigationBar.appearance().tintColor = chromeAccent
    }
}

/// Floating replacement for the system tab bar in the briefing experience:
/// horizontal icon-beside-label items keep the bar vertically compact.
struct CompactTabBar: View {
    struct Item: Identifiable {
        let tab: RootTab
        let label: String
        let icon: String
        let accessibilityIdentifier: String

        var id: RootTab { tab }
    }

    let items: [Item]
    let selection: RootTab
    let onSelect: (RootTab) -> Void

    var body: some View {
        HStack(spacing: 4) {
            ForEach(items) { item in
                itemButton(item)
            }
        }
        .padding(5)
        .background(Capsule().fill(.ultraThinMaterial))
        .overlay {
            Capsule().stroke(Color.outlineVariant.opacity(0.5), lineWidth: 1)
        }
        .frame(maxWidth: 320)
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 6)
        .padding(.bottom, 4)
        .frame(maxWidth: .infinity)
        .accessibilityIdentifier("tabbar.compact")
    }

    private func itemButton(_ item: Item) -> some View {
        let isSelected = item.tab == selection
        return Button {
            onSelect(item.tab)
        } label: {
            HStack(spacing: 6) {
                Image(systemName: item.icon)
                    .font(.appSymbol(size: 13, weight: .semibold))
                Text(item.label)
                    .font(.appCaption.weight(.semibold))
            }
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity)
            .background {
                if isSelected {
                    Capsule().fill(Color.surfaceSecondary)
                }
            }
            .foregroundStyle(isSelected ? Color.appChromeAccent : Color.onSurfaceSecondary)
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(item.label)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
        .accessibilityIdentifier(item.accessibilityIdentifier)
    }
}

@MainActor
enum RootDependencyFactory {
    static func makeAuthenticationViewModel() -> AuthenticationViewModel {
        AuthenticationViewModel(
            authService: AuthenticationService.shared,
            tokenStore: KeychainManager.shared
        )
    }

    static func makeChatSessionsViewModel() -> ChatSessionsViewModel {
        ChatSessionsViewModel(chatService: ChatService.shared)
    }

    static func makeContentListViewModel(
        defaultReadFilter: String = "unread",
        readStateCache: ReadStateCache? = nil
    ) -> ContentListViewModel {
        ContentListViewModel(
            defaultReadFilter: defaultReadFilter,
            contentService: ContentService.shared,
            unreadCountService: UnreadCountService.shared,
            readStateCache: readStateCache
        )
    }

    static func makeContentDetailViewModel(
        contentId: Int = 0,
        contentType: APIContentType? = nil,
        readStateCache: ReadStateCache? = nil
    ) -> ContentDetailViewModel {
        ContentDetailViewModel(
            contentId: contentId,
            contentType: contentType,
            contentService: ContentService.shared,
            feedSubscriptionService: ScraperConfigService.shared,
            toastPresenter: ToastService.shared,
            readStateCache: readStateCache
        )
    }

    static func makeDetailChatCoordinator() -> DetailChatCoordinator {
        DetailChatCoordinator(
            chatSessionManager: ActiveChatSessionManager.shared,
            chatService: ChatService.shared,
            chatRouter: ChatNavigationCoordinator.shared,
            toastPresenter: ToastService.shared
        )
    }

    static func makeDiscussionSheetCoordinator() -> DiscussionSheetCoordinator {
        DiscussionSheetCoordinator(contentService: ContentService.shared)
    }

    static func makePodcastAudioController() -> PodcastAudioController {
        PodcastAudioController(
            playbackService: NarrationPlaybackService.shared,
            audioEpisodeService: AudioEpisodeService.shared
        )
    }

    static func makeCustomNarrationCreationViewModel() -> CustomNarrationCreationViewModel {
        CustomNarrationCreationViewModel(
            audioService: AudioEpisodeService.shared,
            toastPresenter: ToastService.shared
        )
    }

    static func makeCustomNarrationLibraryViewModel(
        readStateCache: ReadStateCache? = nil
    ) -> CustomNarrationLibraryViewModel {
        CustomNarrationLibraryViewModel(
            playbackService: NarrationPlaybackService.shared,
            audioService: AudioEpisodeService.shared,
            unreadCountService: UnreadCountService.shared,
            toastPresenter: ToastService.shared,
            readStateCache: readStateCache
        )
    }

    static func makeSubmissionStatusViewModel(
        defaults: UserDefaults = SharedContainer.userDefaults
    ) -> SubmissionStatusViewModel {
        SubmissionStatusViewModel(defaults: defaults) { cursor in
            try await ContentService.shared.fetchSubmissionStatusList(cursor: cursor)
        }
    }

    static func makeOnboardingViewModel(user: User) -> OnboardingViewModel {
        OnboardingViewModel(
            user: user,
            service: OnboardingService.shared,
            dictationService: SpeechTranscriberFactory.makeVoiceDictationTranscriber(),
            onboardingStateStore: OnboardingStateStore.shared
        )
    }

    static func makeDiscoveryPersonalizeViewModel(userId: Int) -> DiscoveryPersonalizeViewModel {
        DiscoveryPersonalizeViewModel(
            userId: userId,
            service: OnboardingService.shared,
            dictationService: SpeechTranscriberFactory.makeVoiceDictationTranscriber(),
            onboardingStateStore: OnboardingStateStore.shared
        )
    }

    static func makeLearningDecksViewModel() -> LearningDecksViewModel {
        LearningDecksViewModel(service: LearningDeckService.shared)
    }

    static func makeLearningDeckReaderViewModel(
        deck: LearningDeck,
        chatService: (any LearningDeckReaderChatServicing)? = nil
    ) -> LearningDeckReaderViewModel {
        LearningDeckReaderViewModel(
            deck: deck,
            chatService: chatService ?? ChatService.shared,
            deckService: LearningDeckService.shared
        )
    }

    static func makeLearningDeckFocusRecorder() -> LearningDeckFocusRecorder {
        LearningDeckFocusRecorder(
            transcriptionService: SpeechTranscriberFactory.makeVoiceDictationTranscriber(),
            refreshTranscriptionAvailability: {
                await OpenAIService.shared.refreshTranscriptionAvailability()
            }
        )
    }

    static func makeTweetSuggestionsViewModel() -> TweetSuggestionsViewModel {
        TweetSuggestionsViewModel(
            contentService: ContentService.shared,
            twitterService: TwitterShareService.shared,
            transcriptionService: VoiceDictationService.shared,
            authService: AuthenticationService.shared,
            tokenStore: KeychainManager.shared,
            refreshTranscriptionAvailability: {
                await OpenAIService.shared.refreshTranscriptionAvailability()
            },
            setBackendTranscriptionAvailable: { isAvailable in
                AppSettings.shared.setBackendTranscriptionAvailable(isAvailable)
            }
        )
    }

    static func makeKnowledgeHubViewModel() -> KnowledgeHubViewModel {
        KnowledgeHubViewModel(
            chatService: ChatService.shared,
            transcriptionService: SpeechTranscriberFactory.makeVoiceDictationTranscriber(),
            refreshTranscriptionAvailability: {
                await OpenAIService.shared.refreshTranscriptionAvailability()
            }
        )
    }

    static func makeSearchViewModel() -> SearchViewModel {
        SearchViewModel(
            contentService: ContentService.shared,
            scraperConfigService: ScraperConfigService.shared
        )
    }

    static func makeScraperSettingsViewModel(filterTypes: [String]? = nil) -> ScraperSettingsViewModel {
        ScraperSettingsViewModel(
            filterTypes: filterTypes,
            service: ScraperConfigService.shared
        )
    }

    static func makeTabCoordinator(
        userID: Int? = nil,
        readStateCache: ReadStateCache? = nil
    ) -> TabCoordinatorViewModel {
        let readStateCache = readStateCache ?? ReadStateCache()
        let shortFeedRepository = ContentRepository(includeAvailableDates: false)
        let longFeedRepository = ContentRepository(includeAvailableDates: false)
        let readRepository = ReadStatusRepository()
        let newsReadRepository = ReadStatusRepository(endpoint: .newsItems)
        let unreadService = UnreadCountService.shared

        let shortNewsViewModel = ShortNewsListViewModel(
            repository: shortFeedRepository,
            readRepository: newsReadRepository,
            unreadCountService: unreadService,
            readStateCache: readStateCache
        )
        let longContentViewModel = LongContentListViewModel(
            repository: longFeedRepository,
            readRepository: readRepository,
            unreadCountService: unreadService,
            contentService: ContentService.shared,
            toastPresenter: ToastService.shared,
            readStateCache: readStateCache
        )
        let briefingViewModel = BriefingViewModel(
            service: LiveBriefingService(),
            audioEpisodeService: AudioEpisodeService.shared,
            snapshotStore: userID.map { BriefingSnapshotStore(userID: $0) }
        )

        return TabCoordinatorViewModel(
            shortNewsVM: shortNewsViewModel,
            longContentVM: longContentViewModel,
            briefingVM: briefingViewModel
        )
    }
}
