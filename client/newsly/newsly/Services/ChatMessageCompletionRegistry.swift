//
//  ChatMessageCompletionRegistry.swift
//  newsly
//

import Foundation

protocol MessageStatusFetching: AnyObject {
    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse
}

/// Controls the status-request cadence for an asynchronous chat message.
///
/// The live policy preserves the one-minute idle timeout with 36 requests. A newer
/// durable partial cursor resets that idle budget and polls at the fast cadence,
/// bounded by a separate absolute request ceiling.
struct ChatMessageCompletionPollingPolicy: Sendable, Equatable {
    let delaysNanoseconds: [UInt64]
    let progressDelayNanoseconds: UInt64
    let absoluteMaximumRequestCount: Int

    init(
        delaysNanoseconds: [UInt64],
        progressDelayNanoseconds: UInt64 = 500_000_000,
        absoluteMaximumRequestCount: Int? = nil
    ) {
        let idleMaximumRequestCount = delaysNanoseconds.count + 1
        self.delaysNanoseconds = delaysNanoseconds
        self.progressDelayNanoseconds = progressDelayNanoseconds
        self.absoluteMaximumRequestCount = max(
            idleMaximumRequestCount,
            absoluteMaximumRequestCount ?? idleMaximumRequestCount
        )
    }

    static let adaptive = ChatMessageCompletionPollingPolicy(
        delaysNanoseconds:
            Array(repeating: 500_000_000, count: 4)
            + Array(repeating: 1_000_000_000, count: 4)
            + Array(repeating: 2_000_000_000, count: 27),
        absoluteMaximumRequestCount: 120
    )
}

struct ChatPartialResponseCursor: Equatable, Sendable {
    let generation: Int
    let revision: Int
}

struct ChatPartialResponseUpdate: @unchecked Sendable {
    let message: ChatMessage?
    let cursor: ChatPartialResponseCursor
}

/// Owns message-status polling for the whole app and coalesces all observers of a
/// message ID onto one request sequence. Observer cancellation does not immediately
/// destroy shared work: a short orphan grace period lets foreground/background
/// ownership hand off without issuing a duplicate status request.
actor ChatMessageCompletionRegistry {
    typealias FetchStatus = @Sendable (Int) async throws -> MessageStatusResponse
    typealias Sleep = @Sendable (UInt64) async throws -> Void
    typealias ProcessingObserver = @MainActor @Sendable (Int) async -> Void
    typealias PartialObserver = @MainActor @Sendable (ChatPartialResponseUpdate) async -> Void

    private final class ObserverMetadata {
        let onProcessing: ProcessingObserver?
        let onPartial: PartialObserver?
        var lastPartialCursor: ChatPartialResponseCursor?

        init(onProcessing: ProcessingObserver?, onPartial: PartialObserver?) {
            self.onProcessing = onProcessing
            self.onPartial = onPartial
        }
    }

    private typealias ObserverStore = KeyedPollingObserverStore<Int, ChatMessage, ObserverMetadata>

    private typealias CachedOutcome = Result<ChatMessage, Error>

    private let fetchStatus: FetchStatus
    private let policy: ChatMessageCompletionPollingPolicy
    private let sleep: Sleep
    private let orphanGraceNanoseconds: UInt64
    private let terminalCacheLimit: Int

    private var observerStore = ObserverStore()
    private var terminalOutcomes: [Int: CachedOutcome] = [:]
    private var terminalOutcomeOrder: [Int] = []

    init(
        fetchStatus: @escaping FetchStatus,
        policy: ChatMessageCompletionPollingPolicy = .adaptive,
        sleep: @escaping Sleep = { try await Task.sleep(nanoseconds: $0) },
        orphanGraceNanoseconds: UInt64 = 250_000_000,
        terminalCacheLimit: Int = 128
    ) {
        precondition(terminalCacheLimit >= 0)
        self.fetchStatus = fetchStatus
        self.policy = policy
        self.sleep = sleep
        self.orphanGraceNanoseconds = orphanGraceNanoseconds
        self.terminalCacheLimit = terminalCacheLimit
    }

    init(
        statusService: any MessageStatusFetching,
        policy: ChatMessageCompletionPollingPolicy = .adaptive,
        sleep: @escaping Sleep = { try await Task.sleep(nanoseconds: $0) },
        orphanGraceNanoseconds: UInt64 = 250_000_000,
        terminalCacheLimit: Int = 128
    ) {
        self.init(
            fetchStatus: { messageId in
                try await statusService.getMessageStatus(messageId: messageId)
            },
            policy: policy,
            sleep: sleep,
            orphanGraceNanoseconds: orphanGraceNanoseconds,
            terminalCacheLimit: terminalCacheLimit
        )
    }

    nonisolated func waitForCompletion(
        messageId: Int,
        onProcessing: ProcessingObserver? = nil,
        onPartial: PartialObserver? = nil
    ) async throws -> ChatMessage {
        let observerID = UUID()
        let cancellationState = PollingObserverCancellationState()
        return try await withTaskCancellationHandler {
            try Task.checkCancellation()
            let message = try await subscribe(
                messageId: messageId,
                observerID: observerID,
                cancellationState: cancellationState,
                onProcessing: onProcessing,
                onPartial: onPartial
            )
            try Task.checkCancellation()
            return message
        } onCancel: {
            cancellationState.cancel()
            Task {
                await self.cancelObserver(messageId: messageId, observerID: observerID)
            }
        }
    }

    #if DEBUG
    func activeObserverCount(messageId: Int) -> Int {
        observerStore.observerCount(for: messageId)
    }
    #endif

    private func subscribe(
        messageId: Int,
        observerID: UUID,
        cancellationState: PollingObserverCancellationState,
        onProcessing: ProcessingObserver?,
        onPartial: PartialObserver?
    ) async throws -> ChatMessage {
        try await withCheckedThrowingContinuation { continuation in
            if cancellationState.isCancelled {
                continuation.resume(throwing: CancellationError())
                return
            }

            if let outcome = terminalOutcomes[messageId] {
                continuation.resume(with: outcome)
                return
            }

            let observer = ObserverStore.Observer(
                continuation: continuation,
                metadata: ObserverMetadata(
                    onProcessing: onProcessing,
                    onPartial: onPartial
                )
            )
            guard let generation = observerStore.addObserver(
                observer,
                id: observerID,
                for: messageId
            ) else {
                return
            }

            let pollingTask = Task { [weak self] in
                guard let self else { return }
                await self.poll(messageId: messageId, generation: generation)
            }
            observerStore.setPollingTask(
                pollingTask,
                for: messageId,
                generation: generation
            )
        }
    }

    private func cancelObserver(messageId: Int, observerID: UUID) {
        guard let removal = observerStore.removeObserver(
            id: observerID,
            for: messageId
        ) else { return }
        removal.observer.continuation.resume(throwing: CancellationError())
        guard let generation = removal.orphanedGeneration else { return }

        let orphanCancellationTask = Task { [weak self] in
            guard let self else { return }
            if self.orphanGraceNanoseconds > 0 {
                try? await Task.sleep(nanoseconds: self.orphanGraceNanoseconds)
            }
            guard !Task.isCancelled else { return }
            await self.stopPollingIfOrphaned(messageId: messageId, generation: generation)
        }
        observerStore.setOrphanCancellationTask(
            orphanCancellationTask,
            for: messageId,
            generation: generation
        )
    }

    private func stopPollingIfOrphaned(messageId: Int, generation: UUID) {
        observerStore.removeIfOrphaned(key: messageId, generation: generation)?.cancel()
    }

    private func poll(messageId: Int, generation: UUID) async {
        var idleRequestIndex = 0
        var latestCursor: ChatPartialResponseCursor?

        for requestIndex in 0..<policy.absoluteMaximumRequestCount {
            do {
                try Task.checkCancellation()
                guard observerStore.isActive(key: messageId, generation: generation) else { return }

                let status = try await fetchStatus(messageId)
                switch status.status {
                case .completed:
                    guard let assistantMessage = status.assistantMessage else {
                        finish(
                            messageId: messageId,
                            generation: generation,
                            outcome: .failure(ChatServiceError.missingAssistantMessage)
                        )
                        return
                    }
                    finish(
                        messageId: messageId,
                        generation: generation,
                        outcome: .success(assistantMessage)
                    )
                    return

                case .failed:
                    finish(
                        messageId: messageId,
                        generation: generation,
                        outcome: .failure(
                            ChatServiceError.processingFailed(status.error ?? "Unknown error")
                        )
                    )
                    return

                case .processing, .unknown(_):
                    let responseCursor = partialCursor(from: status)
                    let madeProgress = responseCursor.map {
                        isNewerPartialCursor($0, than: latestCursor)
                    } ?? false
                    if let responseCursor {
                        if madeProgress {
                            latestCursor = responseCursor
                        }
                        if responseCursor == latestCursor {
                            await notifyPartial(
                                messageId: messageId,
                                generation: generation,
                                update: ChatPartialResponseUpdate(
                                    message: status.partialAssistantMessage,
                                    cursor: responseCursor
                                )
                            )
                        }
                    }
                    await notifyProcessing(
                        messageId: messageId,
                        generation: generation,
                        attempt: requestIndex + 1
                    )

                    guard requestIndex + 1 < policy.absoluteMaximumRequestCount else {
                        finish(
                            messageId: messageId,
                            generation: generation,
                            outcome: .failure(ChatServiceError.timeout),
                            cachesOutcome: false
                        )
                        return
                    }

                    let delay: UInt64
                    if madeProgress {
                        idleRequestIndex = 0
                        delay = policy.progressDelayNanoseconds
                    } else {
                        guard idleRequestIndex < policy.delaysNanoseconds.count else {
                            finish(
                                messageId: messageId,
                                generation: generation,
                                outcome: .failure(ChatServiceError.timeout),
                                cachesOutcome: false
                            )
                            return
                        }
                        delay = policy.delaysNanoseconds[idleRequestIndex]
                        idleRequestIndex += 1
                    }
                    try await sleep(delay)
                    continue
                }

            } catch where ClientFailure.classify(error) == .cancelled {
                return
            } catch {
                finish(
                    messageId: messageId,
                    generation: generation,
                    outcome: .failure(error),
                    cachesOutcome: false
                )
                return
            }
        }
    }

    private func partialCursor(from status: MessageStatusResponse) -> ChatPartialResponseCursor? {
        guard let generation = status.streamGeneration, let revision = status.streamRevision else {
            return nil
        }
        return ChatPartialResponseCursor(generation: generation, revision: revision)
    }

    private func isNewerPartialCursor(
        _ cursor: ChatPartialResponseCursor,
        than previous: ChatPartialResponseCursor?
    ) -> Bool {
        guard let previous else { return true }
        if cursor.generation != previous.generation {
            return cursor.generation > previous.generation
        }
        return cursor.revision > previous.revision
    }

    private func notifyProcessing(messageId: Int, generation: UUID, attempt: Int) async {
        for observer in observerStore.observers(for: messageId, generation: generation) {
            await observer.metadata.onProcessing?(attempt)
        }
    }

    private func notifyPartial(
        messageId: Int,
        generation: UUID,
        update: ChatPartialResponseUpdate
    ) async {
        for observer in observerStore.observers(for: messageId, generation: generation) {
            guard observer.metadata.lastPartialCursor != update.cursor else { continue }
            observer.metadata.lastPartialCursor = update.cursor
            await observer.metadata.onPartial?(update)
        }
    }

    private func finish(
        messageId: Int,
        generation: UUID,
        outcome: CachedOutcome,
        cachesOutcome: Bool = true
    ) {
        guard let entry = observerStore.removeEntry(
            for: messageId,
            generation: generation
        ) else { return }
        if cachesOutcome {
            cache(outcome, messageId: messageId)
        }
        for observer in entry.observers.values {
            observer.continuation.resume(with: outcome)
        }
    }

    private func cache(_ outcome: CachedOutcome, messageId: Int) {
        guard terminalCacheLimit > 0 else { return }
        terminalOutcomes[messageId] = outcome
        terminalOutcomeOrder.append(messageId)

        if terminalOutcomeOrder.count > terminalCacheLimit {
            let evictedMessageId = terminalOutcomeOrder.removeFirst()
            terminalOutcomes.removeValue(forKey: evictedMessageId)
        }
    }
}
