import Foundation
import Observation

/// Instance-bound construction graph for authenticated presentation state.
///
/// The application entry point supplies the live services once. Feature code
/// receives this graph explicitly and never reaches through a static service
/// locator to construct account-scoped state.
@MainActor
@Observable
final class RootDependencyFactory {
    struct Dependencies {
        let apiClient: APIClient
        let authenticationService: AuthenticationService
        let tokenStore: any AuthTokenStore
        let credentialSession: CredentialSession
        let chatService: ChatService
        let contentService: ContentService
        let scraperConfigService: ScraperConfigService
        let toastService: ToastService
        let briefingService: any BriefingServicing
        let narrationPlaybackService: NarrationPlaybackService
        let audioEpisodeService: AudioEpisodeService
        let onboardingService: OnboardingService
        let onboardingStateStore: OnboardingStateStore
        let learningDeckService: LearningDeckService
        let learningDeckStatusRegistry: LearningDeckStatusRegistry
        let twitterShareService: TwitterShareService
        let openAIService: OpenAIService
        let appSettings: AppSettings
        let xIntegrationService: XIntegrationService
        let feedbackService: FeedbackService
        let cliLinkService: CLILinkService
        let localNotificationService: LocalNotificationService
        let sharedDefaults: UserDefaults
        let speechTranscriber: any SpeechTranscribing
    }

    @ObservationIgnored
    private let dependencies: Dependencies

    init(dependencies: Dependencies) {
        self.dependencies = dependencies
    }

    var newsFilterService: NewsFilterService { NewsFilterService(client: dependencies.apiClient) }

    var authenticationService: AuthenticationService { dependencies.authenticationService }
    var onboardingStateStore: OnboardingStateStore { dependencies.onboardingStateStore }
    var contentService: ContentService { dependencies.contentService }
    var scraperConfigService: ScraperConfigService { dependencies.scraperConfigService }
    var xIntegrationService: XIntegrationService { dependencies.xIntegrationService }
    var feedbackService: FeedbackService { dependencies.feedbackService }
    var cliLinkService: CLILinkService { dependencies.cliLinkService }
    var localNotificationService: LocalNotificationService { dependencies.localNotificationService }
    var narrationPlaybackService: NarrationPlaybackService { dependencies.narrationPlaybackService }
    var learningDeckService: LearningDeckService { dependencies.learningDeckService }
    var appSettings: AppSettings { dependencies.appSettings }
    var toastService: ToastService { dependencies.toastService }

    func makeAuthenticationController() -> AuthenticationController {
        AuthenticationController(
            authService: dependencies.authenticationService,
            tokenStore: dependencies.tokenStore,
            credentialSession: dependencies.credentialSession
        )
    }

    func makeAuthenticatedSession(user: User) -> AuthenticatedSession {
        let badgeStatsStore = BadgeStatsStore(
            fetchStats: { [apiClient = dependencies.apiClient] in
                try await apiClient.request(APIEndpoints.badgeStats)
            }
        )
        let completionRegistry = ChatMessageCompletionRegistry(
            statusService: dependencies.chatService
        )
        let activeChatSessionManager = ActiveChatSessionManager(
            messageCompletionRegistry: completionRegistry,
            notificationService: dependencies.localNotificationService
        )
        let readStateCache = ReadStateCache(
            contentReadRepository: ReadStatusRepository(client: dependencies.apiClient),
            newsReadRepository: ReadStatusRepository(
                client: dependencies.apiClient,
                endpoint: .newsItems
            ),
            badgeStatsStore: badgeStatsStore
        )
        let chatNavigation = ChatNavigationCoordinator()
        dependencies.localNotificationService.setChatRouteHandler {
            [weak chatNavigation] sessionID in
            chatNavigation?.open(ChatSessionRoute(sessionId: sessionID))
        }

        return AuthenticatedSession(
            user: user,
            dependencyFactory: self,
            badgeStatsStore: badgeStatsStore,
            xConnectionStore: XConnectionStore(
                fetchConnection: { [xIntegrationService = dependencies.xIntegrationService] in
                    try await xIntegrationService.fetchConnection()
                }
            ),
            activeChatSessionManager: activeChatSessionManager,
            chatNavigation: chatNavigation,
            readStateCache: readStateCache,
            tabCoordinator: makeTabCoordinator(userID: user.id),
            knowledgeViewModel: makeKnowledgeTimelineViewModel(
                userID: user.id,
                readStateCache: readStateCache,
                badgeStatsStore: badgeStatsStore
            ),
            submissionStatusViewModel: makeSubmissionStatusViewModel(),
            detachNotificationRouting: {
                [localNotificationService = dependencies.localNotificationService] in
                localNotificationService.clearChatRouteHandler()
            }
        )
    }

    func makeChatSessionsViewModel() -> ChatSessionsViewModel {
        ChatSessionsViewModel(chatService: dependencies.chatService)
    }

    func makeContentListViewModel(readStateCache: ReadStateCache) -> ContentListViewModel {
        ContentListViewModel(
            contentService: dependencies.contentService,
            readStateCache: readStateCache
        )
    }

    func makeContentDetailViewModel(
        contentId: Int,
        contentType: APIContentType?,
        readStateCache: ReadStateCache
    ) -> ContentDetailViewModel {
        ContentDetailViewModel(
            contentId: contentId,
            contentType: contentType,
            contentService: dependencies.contentService,
            feedSubscriptionService: dependencies.scraperConfigService,
            toastPresenter: dependencies.toastService,
            readStateCache: readStateCache
        )
    }

    func makeDetailChatCoordinator(
        chatSessionManager: ActiveChatSessionManager,
        chatRouter: ChatNavigationCoordinator
    ) -> DetailChatCoordinator {
        DetailChatCoordinator(
            chatSessionManager: chatSessionManager,
            chatService: dependencies.chatService,
            chatRouter: chatRouter,
            toastPresenter: dependencies.toastService
        )
    }

    func makeDiscussionSummaryCoordinator() -> DiscussionSummaryCoordinator {
        DiscussionSummaryCoordinator(contentService: dependencies.contentService)
    }

    func makeBriefingSourceActionViewModel() -> BriefingSourceActionViewModel {
        BriefingSourceActionViewModel(
            saveSource: { [contentService = dependencies.contentService] source in
                switch source {
                case .news(let id):
                    _ = try await contentService.convertNewsItemToArticle(id: id)
                    return "Saved to Knowledge"
                case .content(let id):
                    let response = try await contentService.saveToKnowledge(id: id)
                    return response.message
                }
            },
            startSourceChat: { [chatService = dependencies.chatService] source in
                let existing = try await chatService.existingSourceChat(for: source)
                try Task.checkCancellation()
                let session: ChatSessionSummary
                if let existing {
                    session = existing
                } else {
                    session = try await chatService.startSourceChat(for: source)
                }
                return ChatSessionRoute(
                    sessionId: session.id, session: session,
                    contentId: source.contentId, newsItemId: source.newsItemId,
                    focusComposerOnAppear: existing == nil
                )
            },
            findSourceDeck: { [deckService = dependencies.learningDeckService] source, sourceURL in
                let decks = try await deckService.listDecks().decks
                return BriefingSourceDeckLookup.find(source: source, sourceURL: sourceURL, decks: decks)
            },
            createSourceDeck: { [deckService = dependencies.learningDeckService] source, focus in
                try await deckService.createDeck(
                    contentId: source.contentId,
                    newsItemId: source.newsItemId,
                    url: nil,
                    interestsPrompt: focus
                )
            }
        )
    }

    func makePodcastAudioController() -> PodcastAudioController {
        PodcastAudioController(
            playbackService: dependencies.narrationPlaybackService,
            audioEpisodeService: dependencies.audioEpisodeService
        )
    }

    func makeSubmissionStatusViewModel() -> SubmissionStatusViewModel {
        SubmissionStatusViewModel(defaults: dependencies.sharedDefaults) {
            [contentService = dependencies.contentService] cursor in
            try await contentService.fetchSubmissionStatusList(cursor: cursor)
        }
    }

    func makeOnboardingViewModel(user: User) -> OnboardingViewModel {
        OnboardingViewModel(
            user: user,
            service: dependencies.onboardingService,
            dictationService: dependencies.speechTranscriber,
            onboardingStateStore: dependencies.onboardingStateStore
        )
    }

    func makeLearningDeckReaderViewModel(
        deck: LearningDeck,
        lifecycle: AppLifecycle,
        activeSessionManager: ActiveChatSessionManager
    ) -> LearningDeckReaderViewModel {
        return LearningDeckReaderViewModel(
            lifecycle: lifecycle,
            deck: deck,
            chatService: dependencies.chatService,
            messageCompletionRegistry: activeSessionManager.messageCompletionRegistry,
            deckService: dependencies.learningDeckService,
            transcriptionService: dependencies.speechTranscriber,
            refreshTranscriptionAvailability: {
                [openAIService = dependencies.openAIService] in
                await openAIService.refreshTranscriptionAvailability()
            },
            deckStatusRegistry: dependencies.learningDeckStatusRegistry
        )
    }

    func makeLearningDeckFocusRecorder() -> LearningDeckFocusRecorder {
        LearningDeckFocusRecorder(
            transcriptionService: dependencies.speechTranscriber,
            refreshTranscriptionAvailability: {
                [openAIService = dependencies.openAIService] in
                await openAIService.refreshTranscriptionAvailability()
            }
        )
    }

    func makeTweetSuggestionsViewModel() -> TweetSuggestionsViewModel {
        TweetSuggestionsViewModel(
            contentService: dependencies.contentService,
            twitterService: dependencies.twitterShareService,
            transcriptionService: dependencies.speechTranscriber,
            refreshTranscriptionAvailability: {
                [openAIService = dependencies.openAIService] in
                await openAIService.refreshTranscriptionAvailability()
            }
        )
    }

    func makeSearchViewModel() -> SearchViewModel {
        SearchViewModel(
            contentService: dependencies.contentService,
            scraperConfigService: dependencies.scraperConfigService
        )
    }

    func makeScraperSettingsViewModel(
        filterTypes: [String]? = nil
    ) -> ScraperSettingsViewModel {
        ScraperSettingsViewModel(
            filterTypes: filterTypes,
            service: dependencies.scraperConfigService
        )
    }

    func makeAssistantFeedOptionActionModel() -> AssistantFeedOptionActionModel {
        AssistantFeedOptionActionModel(
            service: dependencies.scraperConfigService,
            toastPresenter: dependencies.toastService
        )
    }

    func makeChatDependencies(
        activeSessionManager: ActiveChatSessionManager
    ) -> ChatDependencies {
        ChatDependencies(
            chatService: dependencies.chatService,
            messageCompletionRegistry: activeSessionManager.messageCompletionRegistry,
            transcriptionService: dependencies.speechTranscriber,
            activeSessionManager: activeSessionManager,
            refreshTranscriptionAvailability: {
                [openAIService = dependencies.openAIService] in
                await openAIService.refreshTranscriptionAvailability()
            }
        )
    }

    private func makeTabCoordinator(userID: Int) -> TabCoordinatorViewModel {
        TabCoordinatorViewModel(
            briefingVM: BriefingViewModel(
                service: dependencies.briefingService,
                audioEpisodeService: dependencies.audioEpisodeService,
                playbackService: dependencies.narrationPlaybackService,
                snapshotStore: BriefingSnapshotStore(userID: userID)
            ),
            userID: userID,
            defaults: dependencies.sharedDefaults
        )
    }

    private func makeKnowledgeTimelineViewModel(
        userID: Int,
        readStateCache: ReadStateCache,
        badgeStatsStore: BadgeStatsStore
    ) -> KnowledgeTimelineViewModel {
        KnowledgeTimelineViewModel(
            savedContent: makeContentListViewModel(readStateCache: readStateCache),
            chats: KnowledgeChatViewModel(
                chatService: dependencies.chatService,
                transcriptionService: dependencies.speechTranscriber,
                refreshTranscriptionAvailability: {
                    [openAIService = dependencies.openAIService] in
                    await openAIService.refreshTranscriptionAvailability()
                }
            ),
            decks: LearningDecksViewModel(
                service: dependencies.learningDeckService,
                statusRegistry: dependencies.learningDeckStatusRegistry
            ),
            narrations: CustomNarrationLibraryViewModel(
                playbackService: dependencies.narrationPlaybackService,
                audioService: dependencies.audioEpisodeService,
                badgeStatsStore: badgeStatsStore,
                readStateCache: readStateCache
            ),
            saveSignalToken: {
                guard let server = ServerConfigurationDefaults.baseURL(in: SharedContainer.userDefaults) else { return nil }
                return KnowledgeSaveSignalStore(server: server.absoluteString, userID: userID).token
            }
        )
    }
}
