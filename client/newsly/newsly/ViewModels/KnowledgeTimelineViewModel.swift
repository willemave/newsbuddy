//
//  KnowledgeTimelineViewModel.swift
//  newsly
//

import Foundation
import Observation
import os.log

private let knowledgeLifecycleLogger = Logger(
    subsystem: "com.newsly",
    category: "KnowledgeLifecycle"
)

struct KnowledgeFreshnessPolicy: Equatable {
    let revalidationInterval: TimeInterval

    static let standard = KnowledgeFreshnessPolicy(
        revalidationInterval: 5 * 60
    )

    func shouldRevalidate(lastValidatedAt: Date?, at date: Date) -> Bool {
        guard let lastValidatedAt else { return true }
        return date.timeIntervalSince(lastValidatedAt) >= revalidationInterval
    }
}

enum KnowledgeTimelineFailure: Identifiable {
    case savedLoad
    case savedAction(String)
    case chatsLoad
    case chatsAction(String)
    case decksLoad
    case decksAction(String)
    case narrationsLoad
    case narrationsAction(String)

    var id: String {
        switch self {
        case .savedLoad: "saved.load"
        case .savedAction: "saved.action"
        case .chatsLoad: "chats.load"
        case .chatsAction: "chats.action"
        case .decksLoad: "decks.load"
        case .decksAction: "decks.action"
        case .narrationsLoad: "narrations.load"
        case .narrationsAction: "narrations.action"
        }
    }

    var message: String {
        switch self {
        case .savedLoad: "Saved knowledge couldn't be loaded."
        case .savedAction(let message),
             .chatsAction(let message),
             .decksAction(let message),
             .narrationsAction(let message): message
        case .chatsLoad: "Chats couldn't be loaded."
        case .decksLoad: "Learning Decks couldn't be loaded."
        case .narrationsLoad: "Narrations couldn't be loaded."
        }
    }

    var actionTitle: String {
        switch self {
        case .savedLoad, .chatsLoad, .decksLoad, .narrationsLoad: "Try Again"
        case .savedAction, .chatsAction, .decksAction, .narrationsAction: "Dismiss"
        }
    }

    var accessibilityIdentifier: String {
        "knowledge.error.\(id)"
    }
}

@MainActor
@Observable
final class KnowledgeTimelineViewModel {
    let savedContent: ContentListViewModel
    let chats: KnowledgeChatViewModel
    let decks: LearningDecksViewModel
    let narrations: CustomNarrationLibraryViewModel
    private(set) var timeline: [KnowledgeTimelineItem] = []
    private(set) var groupedTimeline: [KnowledgeTimelineDayGroup] = []
    private(set) var automaticReadsEnabled = false
    private(set) var lastValidatedAt: Date?
    private(set) var lastHandledActivationGeneration: UInt64?
    private var isAggregateReadInFlight = false
    private var holdsPublicationBarrier = false
    private var hasFinishedInitialLoad = false

    @ObservationIgnored
    private let freshnessPolicy: KnowledgeFreshnessPolicy
    @ObservationIgnored
    private let now: () -> Date
    @ObservationIgnored
    private var aggregateReadTask: Task<Bool, Never>?
    @ObservationIgnored
    private var aggregateReadGeneration = 0
    @ObservationIgnored
    private var aggregateReadMayContinueInBackground = false
    @ObservationIgnored
    private var automaticReadContextIsActive = false

    init(
        savedContent: ContentListViewModel,
        chats: KnowledgeChatViewModel,
        decks: LearningDecksViewModel,
        narrations: CustomNarrationLibraryViewModel,
        freshnessPolicy: KnowledgeFreshnessPolicy = .standard,
        now: @escaping () -> Date = Date.init
    ) {
        self.savedContent = savedContent
        self.chats = chats
        self.decks = decks
        self.narrations = narrations
        self.freshnessPolicy = freshnessPolicy
        self.now = now
        refreshTimelineProjection()
        observeTimelineSources()
    }

    deinit {
        aggregateReadTask?.cancel()
    }

    var isLoading: Bool {
        !hasFinishedInitialLoad
            || isAggregateReadInFlight
            || chats.isLoading
            || savedContent.isLoading
            || decks.isLoading
            || narrations.isLoading
    }

    var isLoadingMore: Bool {
        chats.isLoadingMore || savedContent.isLoadingMore
    }

    var hasPaginationError: Bool {
        chats.hasLoadMoreError || savedContent.loadMoreErrorMessage != nil
    }

    var failures: [KnowledgeTimelineFailure] {
        var result: [KnowledgeTimelineFailure] = []
        if savedContent.initialLoadErrorMessage != nil {
            result.append(.savedLoad)
        }
        if let message = savedContent.actionErrorMessage {
            result.append(.savedAction(message))
        }
        if chats.loadErrorMessage != nil {
            result.append(.chatsLoad)
        }
        if let message = chats.errorMessage {
            result.append(.chatsAction(message))
        }
        if decks.loadErrorMessage != nil {
            result.append(.decksLoad)
        }
        if let message = decks.errorMessage {
            result.append(.decksAction(message))
        }
        if narrations.loadErrorMessage != nil {
            result.append(.narrationsLoad)
        }
        if let message = narrations.errorMessage {
            result.append(.narrationsAction(message))
        }
        return result
    }

    /// Activates the visible Knowledge root for one process activation.
    /// Repeated calls for the same generation are no-ops.
    func activate(_ activation: AppLifecycle.Activation) async {
        automaticReadContextIsActive = true

        guard lastHandledActivationGeneration != activation.generation
                || holdsPublicationBarrier else {
            knowledgeLifecycleLogger.debug(
                "Activation skipped | generation=\(activation.generation, privacy: .public) reason=already_handled"
            )
            enableAutomaticObservation()
            return
        }

        let needsInitialLoad = !hasFinishedInitialLoad
        if !needsInitialLoad,
           !holdsPublicationBarrier,
           !freshnessPolicy.shouldRevalidate(
               lastValidatedAt: lastValidatedAt,
               at: activation.occurredAt
           ) {
            lastHandledActivationGeneration = activation.generation
            knowledgeLifecycleLogger.info(
                "Activation skipped | generation=\(activation.generation, privacy: .public) reason=fresh"
            )
            enableAutomaticObservation()
            return
        }

        knowledgeLifecycleLogger.info(
            "Activation read started | generation=\(activation.generation, privacy: .public) initial=\(needsInitialLoad, privacy: .public)"
        )
        automaticReadsEnabled = false
        let completed = await runAggregateRead(
            isInitial: needsInitialLoad,
            mayContinueInBackground: false
        )
        guard completed, automaticReadContextIsActive else { return }
        lastHandledActivationGeneration = activation.generation
        knowledgeLifecycleLogger.info(
            "Activation read finished | generation=\(activation.generation, privacy: .public) validated=\(self.sourceLoadsSucceeded, privacy: .public)"
        )
        enableAutomaticObservation()
    }

    /// Explicit user refresh. Unlike lifecycle work, it is not cancelled just
    /// because the app crosses a background boundary after the gesture began.
    func forceReload() async {
        _ = await runAggregateRead(
            isInitial: !hasFinishedInitialLoad,
            mayContinueInBackground: true
        )
    }

    /// Cancels only automatic reads and observation. Commands and accepted
    /// server work keep their existing durable ownership.
    func suspendAutomaticReads() {
        let hadAutomaticWork = automaticReadContextIsActive
            || automaticReadsEnabled
            || aggregateReadTask != nil
        if hadAutomaticWork {
            knowledgeLifecycleLogger.info("Automatic reads suspended")
        }
        automaticReadContextIsActive = false
        automaticReadsEnabled = false
        decks.suspendAutomaticObservation()
        narrations.suspendAutomaticObservation()

        guard aggregateReadTask != nil,
              !aggregateReadMayContinueInBackground else {
            return
        }
        cancelAggregateRead(generation: aggregateReadGeneration)
    }

    func loadNextPage() async {
        let savedOldest = savedContent.hasMoreContent
            ? savedContent.contents.last?.knowledgeActivityDate
            : nil
        let chatOldest = chats.hasMoreSessions
            ? chats.sessions.last.map { $0.lastActivityDate ?? $0.createdAt }
            : nil
        switch KnowledgePaginationSource.next(
            savedOldest: savedOldest,
            chatOldest: chatOldest
        ) {
        case .saved:
            await savedContent.loadMoreContent()
        case .chats:
            await chats.loadMoreSessions()
        case nil:
            break
        }
        refreshTimelineProjection()
    }

    func recover(_ failure: KnowledgeTimelineFailure) async {
        switch failure {
        case .savedLoad:
            await savedContent.revalidateKnowledgeLibrary()
        case .savedAction:
            savedContent.clearActionError()
        case .chatsLoad:
            await chats.loadChats()
        case .chatsAction:
            chats.clearError()
        case .decksLoad:
            await decks.load()
        case .decksAction:
            decks.clearError()
        case .narrationsLoad:
            await narrations.load()
        case .narrationsAction:
            narrations.clearError()
        }
        refreshTimelineProjection()
    }

    func cancelTransientWork() {
        chats.cancelVoiceRecording()
    }

    private func observeTimelineSources() {
        withObservationTracking {
            _ = savedContent.contents
            _ = chats.sessions
            _ = decks.decks
            _ = narrations.episodes
        } onChange: { [weak self] in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if !self.holdsPublicationBarrier {
                    self.refreshTimelineProjection()
                }
                self.observeTimelineSources()
            }
        }
    }

    private func runAggregateRead(
        isInitial: Bool,
        mayContinueInBackground: Bool
    ) async -> Bool {
        if let aggregateReadTask {
            if mayContinueInBackground {
                aggregateReadMayContinueInBackground = true
            }
            return await awaitAggregateRead(
                aggregateReadTask,
                generation: aggregateReadGeneration
            )
        }

        aggregateReadGeneration &+= 1
        let generation = aggregateReadGeneration
        isAggregateReadInFlight = true
        holdsPublicationBarrier = true
        aggregateReadMayContinueInBackground = mayContinueInBackground
        cancelObsoleteChildReads()

        let task = Task { @MainActor [weak self] in
            guard let self else { return false }
            let completed = await self.performSourceRead(isInitial: isInitial)
            return self.finishAggregateRead(
                generation: generation,
                completed: completed
            )
        }
        aggregateReadTask = task

        return await awaitAggregateRead(task, generation: generation)
    }

    private func awaitAggregateRead(
        _ task: Task<Bool, Never>,
        generation: Int
    ) async -> Bool {
        await withTaskCancellationHandler {
            await task.value
        } onCancel: {
            Task { @MainActor [weak self] in
                self?.cancelAggregateRead(generation: generation)
            }
        }
    }

    private func finishAggregateRead(
        generation: Int,
        completed: Bool
    ) -> Bool {
        guard generation == aggregateReadGeneration else { return false }

        aggregateReadTask = nil
        aggregateReadMayContinueInBackground = false
        isAggregateReadInFlight = false

        if completed {
            hasFinishedInitialLoad = true
            if sourceLoadsSucceeded {
                lastValidatedAt = now()
            }
        }

        // Publish all four source results in one commit. This remains true for
        // partial success and keeps the K13 initial-load barrier intact.
        holdsPublicationBarrier = false
        refreshTimelineProjection()
        return completed
    }

    private func cancelAggregateRead(generation: Int) {
        guard generation == aggregateReadGeneration,
              aggregateReadTask != nil,
              !aggregateReadMayContinueInBackground else {
            return
        }
        aggregateReadGeneration &+= 1
        aggregateReadTask?.cancel()
        aggregateReadTask = nil
        aggregateReadMayContinueInBackground = false
        isAggregateReadInFlight = false
        cancelObsoleteChildReads()
        // Initial publication remains all-or-nothing. Once a prior aggregate
        // exists, however, cancellation must not leave ordinary source
        // observation suppressed until some future successful refresh.
        holdsPublicationBarrier = !hasFinishedInitialLoad
        knowledgeLifecycleLogger.debug(
            "Aggregate read cancelled | generation=\(generation, privacy: .public)"
        )
    }

    private func cancelObsoleteChildReads() {
        savedContent.cancelAutomaticRead()
        chats.cancelAutomaticRead()
        decks.cancelListRead()
        narrations.cancelListRead()
    }

    private func enableAutomaticObservation() {
        guard automaticReadContextIsActive else { return }
        automaticReadsEnabled = true
        decks.resumeAutomaticObservation()
        narrations.resumeAutomaticObservation()
    }

    private func performSourceRead(isInitial: Bool) async -> Bool {
        async let savedLoad: Void = loadSavedContent(isInitial: isInitial)
        async let chatLoad: Void = chats.loadChats()
        async let narrationLoad: Void = narrations.load()
        async let deckLoad: Void = decks.load()
        _ = await (savedLoad, chatLoad, narrationLoad, deckLoad)
        return !Task.isCancelled
    }

    private func loadSavedContent(isInitial: Bool) async {
        if isInitial {
            await savedContent.loadKnowledgeLibrary()
        } else {
            await savedContent.revalidateKnowledgeLibrary()
        }
    }

    private var sourceLoadsSucceeded: Bool {
        savedContent.initialLoadErrorMessage == nil
            && chats.loadErrorMessage == nil
            && decks.loadErrorMessage == nil
            && narrations.loadErrorMessage == nil
    }

    private func refreshTimelineProjection() {
        timeline = KnowledgeTimelineItem.merged(
            saved: savedContent.contents,
            chats: chats.sessions,
            decks: decks.decks,
            narrations: narrations.episodes
        )
        groupedTimeline = KnowledgeTimelineDayGroup.group(timeline)
    }
}
