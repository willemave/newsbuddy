import Foundation

/// Transitional construction helpers for route-owned presentation models.
/// Root authenticated stores are owned by `AuthenticatedSession`; callers
/// should prefer exact initializer dependencies over adding new helpers here.
@MainActor
enum RootDependencyFactory {
    static func makeAuthenticationViewModel() -> AuthenticationViewModel {
        AuthenticationViewModel(
            authService: AuthenticationService.shared,
            tokenStore: KeychainManager.shared,
            credentialSession: CredentialSession.shared
        )
    }

    static func makeChatSessionsViewModel() -> ChatSessionsViewModel {
        ChatSessionsViewModel(chatService: ChatService.shared)
    }

    static func makeContentListViewModel(
        readStateCache: ReadStateCache? = nil
    ) -> ContentListViewModel {
        ContentListViewModel(
            contentService: ContentService.shared,
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

    static func makeDetailChatCoordinator(
        chatSessionManager: ActiveChatSessionManager,
        chatRouter: any ChatRouteOpening
    ) -> DetailChatCoordinator {
        DetailChatCoordinator(
            chatSessionManager: chatSessionManager,
            chatService: ChatService.shared,
            chatRouter: chatRouter,
            toastPresenter: ToastService.shared
        )
    }

    static func makeDiscussionSummaryCoordinator() -> DiscussionSummaryCoordinator {
        DiscussionSummaryCoordinator(contentService: ContentService.shared)
    }

    static func makePodcastAudioController() -> PodcastAudioController {
        PodcastAudioController(
            playbackService: NarrationPlaybackService.shared,
            audioEpisodeService: AudioEpisodeService.shared
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

    static func makeLearningDeckReaderViewModel(
        deck: LearningDeck,
        lifecycle: AppLifecycle,
        activeSessionManager: ActiveChatSessionManager,
        chatService: (any LearningDeckReaderChatServicing)? = nil
    ) -> LearningDeckReaderViewModel {
        let resolvedChatService = chatService ?? ChatService.shared
        let messageCompletionRegistry = chatService.map {
            ChatMessageCompletionRegistry(statusService: $0)
        } ?? activeSessionManager.messageCompletionRegistry
        return LearningDeckReaderViewModel(
            lifecycle: lifecycle,
            deck: deck,
            chatService: resolvedChatService,
            messageCompletionRegistry: messageCompletionRegistry,
            deckService: LearningDeckService.shared,
            deckStatusRegistry: LearningDeckStatusRegistry.shared
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
            transcriptionService: SpeechTranscriberFactory.makeVoiceDictationTranscriber(),
            refreshTranscriptionAvailability: {
                await OpenAIService.shared.refreshTranscriptionAvailability()
            },
            setBackendTranscriptionAvailable: { isAvailable in
                AppSettings.shared.setBackendTranscriptionAvailable(isAvailable)
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
}
