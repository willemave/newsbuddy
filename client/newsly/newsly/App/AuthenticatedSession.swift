import Foundation
import Observation

/// Owns state whose lifetime is exactly one authenticated Newsly account.
///
/// Route-owned models remain view-owned. This scope owns the root stores and
/// process-wide pollers so account replacement can end their work explicitly.
@MainActor
@Observable
final class AuthenticatedSession {
    private(set) var user: User

    let badgeStatsStore: BadgeStatsStore
    let activeChatSessionManager: ActiveChatSessionManager
    let chatNavigation: ChatNavigationCoordinator
    let readingStateStore: ReadingStateStore
    let readStateCache: ReadStateCache
    let tabCoordinator: TabCoordinatorViewModel
    let knowledgeViewModel: KnowledgeTimelineViewModel
    let submissionStatusViewModel: SubmissionStatusViewModel

    @ObservationIgnored
    private var handledActivationGeneration: UInt64?
    @ObservationIgnored
    private var isDetached = false

    init(
        user: User,
        badgeStatsStore: BadgeStatsStore? = nil,
        activeChatSessionManager: ActiveChatSessionManager? = nil,
        chatNavigation: ChatNavigationCoordinator? = nil,
        readingStateStore: ReadingStateStore? = nil,
        readStateCache: ReadStateCache? = nil,
        tabCoordinator: TabCoordinatorViewModel? = nil,
        knowledgeViewModel: KnowledgeTimelineViewModel? = nil,
        submissionStatusViewModel: SubmissionStatusViewModel? = nil
    ) {
        self.user = user

        let badgeStatsStore = badgeStatsStore
            ?? BadgeStatsStore(notificationCenter: NotificationCenter())
        let activeChatSessionManager = activeChatSessionManager
            ?? {
                let completionRegistry = ChatMessageCompletionRegistry(
                    statusService: ChatService.shared
                )
                return ActiveChatSessionManager(
                    messageCompletionRegistry: completionRegistry,
                    observesAuthenticationNotifications: false
                )
            }()
        let readStateCache = readStateCache
            ?? ReadStateCache(badgeStatsStore: badgeStatsStore)

        self.badgeStatsStore = badgeStatsStore
        self.activeChatSessionManager = activeChatSessionManager
        // Notification routing is process-owned today; the session clears the
        // shared sink at its lifetime boundary until that producer is injected.
        self.chatNavigation = chatNavigation ?? .shared
        self.readingStateStore = readingStateStore ?? ReadingStateStore(userId: user.id)
        self.readStateCache = readStateCache
        self.tabCoordinator = tabCoordinator
            ?? Self.makeTabCoordinator(userID: user.id)
        self.knowledgeViewModel = knowledgeViewModel
            ?? Self.makeKnowledgeTimelineViewModel(
                readStateCache: readStateCache,
                badgeStatsStore: badgeStatsStore
            )
        self.submissionStatusViewModel = submissionStatusViewModel
            ?? SubmissionStatusViewModel(defaults: SharedContainer.userDefaults) { cursor in
                try await ContentService.shared.fetchSubmissionStatusList(cursor: cursor)
            }
    }

    func updateUser(_ user: User) {
        guard user.id == self.user.id else { return }
        self.user = user
    }

    /// Receives process lifecycle facts once, from `AppRuntime`.
    func synchronize(with lifecycle: AppLifecycle) {
        guard !isDetached else { return }
        switch lifecycle.phase {
        case .active:
            guard
                let generation = lifecycle.activation?.generation,
                generation != handledActivationGeneration
            else {
                return
            }
            handledActivationGeneration = generation
            activeChatSessionManager.setPollingSuspended(false)
            badgeStatsStore.activate()
        case .inactive:
            break
        case .background:
            badgeStatsStore.suspend()
            activeChatSessionManager.setPollingSuspended(true)
        }
    }

    /// Ends all work owned by this authenticated account.
    func detach() {
        guard !isDetached else { return }
        isDetached = true
        badgeStatsStore.suspend()
        activeChatSessionManager.setPollingSuspended(true)
        activeChatSessionManager.reset()
        chatNavigation.clear()
        tabCoordinator.briefingVM.setActive(false)
        handledActivationGeneration = nil
    }

    private static func makeTabCoordinator(userID: Int) -> TabCoordinatorViewModel {
        TabCoordinatorViewModel(
            briefingVM: BriefingViewModel(
                service: LiveBriefingService(),
                audioEpisodeService: AudioEpisodeService.shared,
                playbackService: NarrationPlaybackService.shared,
                snapshotStore: BriefingSnapshotStore(userID: userID)
            ),
            userID: userID
        )
    }

    private static func makeKnowledgeTimelineViewModel(
        readStateCache: ReadStateCache,
        badgeStatsStore: BadgeStatsStore
    ) -> KnowledgeTimelineViewModel {
        KnowledgeTimelineViewModel(
            savedContent: ContentListViewModel(
                contentService: ContentService.shared,
                readStateCache: readStateCache
            ),
            chats: KnowledgeChatViewModel(
                chatService: ChatService.shared,
                transcriptionService: SpeechTranscriberFactory.makeVoiceDictationTranscriber(),
                refreshTranscriptionAvailability: {
                    await OpenAIService.shared.refreshTranscriptionAvailability()
                }
            ),
            decks: LearningDecksViewModel(
                service: LearningDeckService.shared,
                statusRegistry: LearningDeckStatusRegistry.shared
            ),
            narrations: CustomNarrationLibraryViewModel(
                playbackService: NarrationPlaybackService.shared,
                audioService: AudioEpisodeService.shared,
                badgeStatsStore: badgeStatsStore,
                toastPresenter: ToastService.shared,
                readStateCache: readStateCache
            )
        )
    }
}
