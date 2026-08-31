//
//  LearningDeckStatusRegistry.swift
//  newsly
//

import Foundation

protocol LearningDeckStatusFetching: AnyObject {
    func fetchDeck(id: Int) async throws -> LearningDeck
}

struct LearningDeckStatusPollingPolicy: Sendable, Equatable {
    let delaysNanoseconds: [UInt64]

    var maximumRequestCount: Int {
        delaysNanoseconds.count + 1
    }

    static let adaptive = LearningDeckStatusPollingPolicy(
        delaysNanoseconds:
            Array(repeating: 1_000_000_000, count: 4)
            + Array(repeating: 3_000_000_000, count: 8)
            + Array(repeating: 5_000_000_000, count: 67)
    )

    static func fixed(intervalNanoseconds: UInt64, attemptLimit: Int) -> Self {
        Self(
            delaysNanoseconds: Array(
                repeating: intervalNanoseconds,
                count: max(attemptLimit - 1, 0)
            )
        )
    }
}

enum LearningDeckStatusRegistryError: LocalizedError, Equatable {
    case timeout

    var errorDescription: String? {
        "Your deck is still being prepared."
    }
}

/// Owns generation-status polling for the whole app and coalesces observers by deck ID.
/// Outcomes are intentionally not cached because regenerating a deck reuses its ID.
actor LearningDeckStatusRegistry {
    typealias FetchDeck = @Sendable (Int) async throws -> LearningDeck
    typealias Sleep = @Sendable (UInt64) async throws -> Void
    typealias UpdateObserver = @MainActor @Sendable (LearningDeck) async -> Void
    typealias RetryObserver = @MainActor @Sendable (Error) async -> Void

    private struct ObserverMetadata {
        let onUpdate: UpdateObserver?
        let onRetry: RetryObserver?
    }

    private typealias ObserverStore = KeyedPollingObserverStore<Int, LearningDeck, ObserverMetadata>

    static let shared = LearningDeckStatusRegistry(statusService: LearningDeckService.shared)

    private let fetchDeck: FetchDeck
    private let policy: LearningDeckStatusPollingPolicy
    private let sleep: Sleep
    private let orphanGraceNanoseconds: UInt64
    private var observerStore = ObserverStore()

    init(
        fetchDeck: @escaping FetchDeck,
        policy: LearningDeckStatusPollingPolicy = .adaptive,
        sleep: @escaping Sleep = { try await Task.sleep(nanoseconds: $0) },
        orphanGraceNanoseconds: UInt64 = 250_000_000
    ) {
        self.fetchDeck = fetchDeck
        self.policy = policy
        self.sleep = sleep
        self.orphanGraceNanoseconds = orphanGraceNanoseconds
    }

    init(
        statusService: any LearningDeckStatusFetching,
        policy: LearningDeckStatusPollingPolicy = .adaptive,
        sleep: @escaping Sleep = { try await Task.sleep(nanoseconds: $0) },
        orphanGraceNanoseconds: UInt64 = 250_000_000
    ) {
        self.init(
            fetchDeck: { try await statusService.fetchDeck(id: $0) },
            policy: policy,
            sleep: sleep,
            orphanGraceNanoseconds: orphanGraceNanoseconds
        )
    }

    nonisolated func waitUntilTerminal(
        deckId: Int,
        onUpdate: UpdateObserver? = nil,
        onRetry: RetryObserver? = nil
    ) async throws -> LearningDeck {
        let observerID = UUID()
        let cancellationState = PollingObserverCancellationState()
        return try await withTaskCancellationHandler {
            try Task.checkCancellation()
            let deck = try await subscribe(
                deckId: deckId,
                observerID: observerID,
                cancellationState: cancellationState,
                onUpdate: onUpdate,
                onRetry: onRetry
            )
            try Task.checkCancellation()
            return deck
        } onCancel: {
            cancellationState.cancel()
            Task {
                await self.cancelObserver(deckId: deckId, observerID: observerID)
            }
        }
    }

    func invalidate(deckId: Int) {
        guard let entry = observerStore.removeEntry(for: deckId) else { return }
        for observer in entry.observers.values {
            observer.continuation.resume(throwing: CancellationError())
        }
    }

    #if DEBUG
    func activeObserverCount(deckId: Int) -> Int {
        observerStore.observerCount(for: deckId)
    }
    #endif

    private func subscribe(
        deckId: Int,
        observerID: UUID,
        cancellationState: PollingObserverCancellationState,
        onUpdate: UpdateObserver?,
        onRetry: RetryObserver?
    ) async throws -> LearningDeck {
        try await withCheckedThrowingContinuation { continuation in
            guard !cancellationState.isCancelled else {
                continuation.resume(throwing: CancellationError())
                return
            }

            let observer = ObserverStore.Observer(
                continuation: continuation,
                metadata: ObserverMetadata(onUpdate: onUpdate, onRetry: onRetry)
            )
            guard let generation = observerStore.addObserver(
                observer,
                id: observerID,
                for: deckId
            ) else {
                return
            }

            let pollingTask = Task { [weak self] in
                guard let self else { return }
                await self.poll(deckId: deckId, generation: generation)
            }
            observerStore.setPollingTask(
                pollingTask,
                for: deckId,
                generation: generation
            )
        }
    }

    private func cancelObserver(deckId: Int, observerID: UUID) {
        guard let removal = observerStore.removeObserver(
            id: observerID,
            for: deckId
        ) else { return }
        removal.observer.continuation.resume(throwing: CancellationError())
        guard let generation = removal.orphanedGeneration else { return }

        let orphanCancellationTask = Task { [weak self] in
            guard let self else { return }
            if self.orphanGraceNanoseconds > 0 {
                try? await Task.sleep(nanoseconds: self.orphanGraceNanoseconds)
            }
            guard !Task.isCancelled else { return }
            await self.stopPollingIfOrphaned(deckId: deckId, generation: generation)
        }
        observerStore.setOrphanCancellationTask(
            orphanCancellationTask,
            for: deckId,
            generation: generation
        )
    }

    private func stopPollingIfOrphaned(deckId: Int, generation: UUID) {
        observerStore.removeIfOrphaned(key: deckId, generation: generation)?.cancel()
    }

    private func poll(deckId: Int, generation: UUID) async {
        for requestIndex in 0..<policy.maximumRequestCount {
            do {
                try Task.checkCancellation()
                guard observerStore.isActive(key: deckId, generation: generation) else { return }
                let deck = try await fetchDeck(deckId)
                await notifyUpdate(deckId: deckId, generation: generation, deck: deck)
                if !deck.hasActiveLatestRun {
                    finish(deckId: deckId, generation: generation, result: .success(deck))
                    return
                }
            } catch where ClientFailure.classify(error) == .cancelled {
                return
            } catch {
                guard isRetryableLearningDeckStatusError(error) else {
                    finish(deckId: deckId, generation: generation, result: .failure(error))
                    return
                }
                await notifyRetry(deckId: deckId, generation: generation, error: error)
            }

            guard requestIndex < policy.delaysNanoseconds.count else {
                finish(
                    deckId: deckId,
                    generation: generation,
                    result: .failure(LearningDeckStatusRegistryError.timeout)
                )
                return
            }
            do {
                try await sleep(policy.delaysNanoseconds[requestIndex])
            } catch where ClientFailure.classify(error) == .cancelled {
                return
            } catch {
                finish(deckId: deckId, generation: generation, result: .failure(error))
                return
            }
        }
    }

    private func notifyUpdate(deckId: Int, generation: UUID, deck: LearningDeck) async {
        for observer in observerStore.observers(for: deckId, generation: generation) {
            await observer.metadata.onUpdate?(deck)
        }
    }

    private func notifyRetry(deckId: Int, generation: UUID, error: Error) async {
        for observer in observerStore.observers(for: deckId, generation: generation) {
            await observer.metadata.onRetry?(error)
        }
    }

    private func finish(
        deckId: Int,
        generation: UUID,
        result: Result<LearningDeck, Error>
    ) {
        guard let entry = observerStore.removeEntry(
            for: deckId,
            generation: generation
        ) else { return }
        for observer in entry.observers.values {
            observer.continuation.resume(with: result)
        }
    }
}

private func isRetryableLearningDeckStatusError(_ error: Error) -> Bool {
    switch ClientFailure.classify(error) {
    case .cancelled,
         .authenticationRequired,
         .authenticationExpired,
         .invalidRequest,
         .decoding:
        return false
    case .http(let statusCode, _):
        return statusCode == 408
            || statusCode == 409
            || statusCode == 425
            || statusCode == 429
            || statusCode >= 500
    case .server(_, let error):
        return error.retryable
    case .connectivity, .invalidResponse, .unexpected:
        return true
    }
}
