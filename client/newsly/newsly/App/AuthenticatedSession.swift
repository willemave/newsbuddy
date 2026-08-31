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

    let dependencyFactory: RootDependencyFactory
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
    @ObservationIgnored
    private let detachNotificationRouting: @MainActor () -> Void

    init(
        user: User,
        dependencyFactory: RootDependencyFactory,
        badgeStatsStore: BadgeStatsStore,
        activeChatSessionManager: ActiveChatSessionManager,
        chatNavigation: ChatNavigationCoordinator,
        readingStateStore: ReadingStateStore,
        readStateCache: ReadStateCache,
        tabCoordinator: TabCoordinatorViewModel,
        knowledgeViewModel: KnowledgeTimelineViewModel,
        submissionStatusViewModel: SubmissionStatusViewModel,
        detachNotificationRouting: @escaping @MainActor () -> Void
    ) {
        self.user = user
        self.dependencyFactory = dependencyFactory
        self.badgeStatsStore = badgeStatsStore
        self.activeChatSessionManager = activeChatSessionManager
        self.chatNavigation = chatNavigation
        self.readingStateStore = readingStateStore
        self.readStateCache = readStateCache
        self.tabCoordinator = tabCoordinator
        self.knowledgeViewModel = knowledgeViewModel
        self.submissionStatusViewModel = submissionStatusViewModel
        self.detachNotificationRouting = detachNotificationRouting
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
        detachNotificationRouting()
        tabCoordinator.briefingVM.setActive(false)
        handledActivationGeneration = nil
    }

}
