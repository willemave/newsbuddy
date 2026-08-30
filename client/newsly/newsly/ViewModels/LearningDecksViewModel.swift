//
//  LearningDecksViewModel.swift
//  newsly
//

import Foundation
import Observation

private enum LearningDecksTaskKey: Hashable {
    case deckPolling(Int)
}

private enum LearningDeckMutation {
    case upsert(LearningDeck)
    case remove
}

private struct VersionedLearningDeckMutation {
    let revision: Int
    let mutation: LearningDeckMutation
}

@MainActor
@Observable
final class LearningDecksViewModel {
    private(set) var decks: [LearningDeck] = []
    private(set) var isLoading = false
    private(set) var isCreating = false
    private(set) var busyDeckIDs: Set<Int> = []
    private(set) var loadErrorMessage: String?
    var errorMessage: String?

    @ObservationIgnored
    private let service: any LearningDeckServicing
    @ObservationIgnored
    private let tasks = TaskBag<LearningDecksTaskKey>()
    @ObservationIgnored
    private let statusRegistry: LearningDeckStatusRegistry
    @ObservationIgnored
    private var mutationRevision = 0
    @ObservationIgnored
    private var deckMutations: [Int: VersionedLearningDeckMutation] = [:]
    @ObservationIgnored
    private var activeLoadRequestID: UUID?

    init(
        service: any LearningDeckServicing,
        statusRegistry: LearningDeckStatusRegistry? = nil,
        pollingIntervalNanoseconds: UInt64 = 3_000_000_000,
        pollingAttemptLimit: Int = 120
    ) {
        self.service = service
        self.statusRegistry = statusRegistry ?? LearningDeckStatusRegistry(
            statusService: service,
            policy: .fixed(
                intervalNanoseconds: pollingIntervalNanoseconds,
                attemptLimit: pollingAttemptLimit
            )
        )
    }

    deinit {
        tasks.cancelAll()
    }

    func load() async {
        guard activeLoadRequestID == nil else { return }
        let requestID = UUID()
        let requestStartRevision = mutationRevision
        activeLoadRequestID = requestID
        isLoading = true
        loadErrorMessage = nil
        defer {
            if activeLoadRequestID == requestID {
                activeLoadRequestID = nil
                isLoading = false
            }
        }

        do {
            let response = try await service.listDecks()
            guard activeLoadRequestID == requestID, !Task.isCancelled else { return }
            decks = reconcileLoadedDecks(
                response.decks,
                requestStartRevision: requestStartRevision
            )
            decks.forEach(startPollingIfNeeded)
        } catch where ClientFailure.classify(error) == .cancelled {
            return
        } catch {
            guard activeLoadRequestID == requestID, !Task.isCancelled else { return }
            loadErrorMessage = error.localizedDescription
        }
    }

    func suspendAutomaticObservation() {
        tasks.cancelAll()
    }

    func cancelListRead() {
        activeLoadRequestID = nil
        isLoading = false
    }

    func resumeAutomaticObservation() {
        decks.forEach(startPollingIfNeeded)
    }

    func clearError() {
        errorMessage = nil
    }

    func createDeck(
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        url: String? = nil,
        interestsPrompt: String? = nil
    ) async -> LearningDeck? {
        guard !isCreating else { return nil }
        isCreating = true
        defer { isCreating = false }

        do {
            let deck = try await service.createDeck(
                contentId: contentId,
                newsItemId: newsItemId,
                url: url,
                interestsPrompt: interestsPrompt
            )
            upsert(deck)
            errorMessage = nil
            return deck
        } catch where ClientFailure.classify(error) == .cancelled {
            return nil
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    func refresh(_ deck: LearningDeck) async {
        await withDeckBusy(deck.id) {
            do {
                let latest = try await service.fetchDeck(id: deck.id)
                upsert(latest)
                errorMessage = nil
            } catch where ClientFailure.classify(error) == .cancelled {
                return
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    func viewerURL(for deck: LearningDeck) async -> URL? {
        await deckURL(for: deck, request: service.viewerURL(deckId:))
    }

    func sourceNotesURL(for deck: LearningDeck) async -> URL? {
        await deckURL(for: deck, request: service.sourceNotesURL(deckId:))
    }

    private func deckURL(
        for deck: LearningDeck,
        request: (Int) async throws -> URL
    ) async -> URL? {
        await withDeckBusy(deck.id) {
            do {
                let url = try await request(deck.id)
                errorMessage = nil
                return url
            } catch where ClientFailure.classify(error) == .cancelled {
                return nil
            } catch {
                errorMessage = error.localizedDescription
                return nil
            }
        }
    }

    func toggleShare(for deck: LearningDeck) async -> String? {
        await withDeckBusy(deck.id) {
            do {
                let shareResponse = deck.shareEnabled
                    ? try await service.disableShare(deckId: deck.id)
                    : try await service.enableShare(deckId: deck.id)
                let latest = try await service.fetchDeck(id: deck.id)
                upsert(latest)
                errorMessage = nil
                return shareResponse.shareURL
            } catch where ClientFailure.classify(error) == .cancelled {
                return nil
            } catch {
                errorMessage = error.localizedDescription
                return nil
            }
        }
    }

    /// Re-runs a deck from its original source and existing focus.
    func regenerate(_ deck: LearningDeck) async -> LearningDeck? {
        await withDeckBusy(deck.id) {
            let trimmedURL = deck.sourceURL?.trimmingCharacters(in: .whitespacesAndNewlines)
            let interestsPrompt = deck.latestRun?.interestsPrompt

            do {
                let replacement: LearningDeck
                if let contentId = deck.sourceContentId {
                    replacement = try await service.createDeck(
                        contentId: contentId,
                        newsItemId: nil,
                        url: nil,
                        interestsPrompt: interestsPrompt
                    )
                } else if let url = trimmedURL, !url.isEmpty {
                    replacement = try await service.createDeck(
                        contentId: nil,
                        newsItemId: nil,
                        url: url,
                        interestsPrompt: interestsPrompt
                    )
                } else {
                    errorMessage = "This deck can't be regenerated."
                    return nil
                }
                await statusRegistry.invalidate(deckId: deck.id)
                tasks.cancel(.deckPolling(deck.id))
                upsert(replacement)
                errorMessage = nil
                return replacement
            } catch where ClientFailure.classify(error) == .cancelled {
                return nil
            } catch {
                errorMessage = error.localizedDescription
                return nil
            }
        }
    }

    func delete(_ deck: LearningDeck) async {
        await withDeckBusy(deck.id) {
            do {
                try await service.deleteDeck(deckId: deck.id)
                await statusRegistry.invalidate(deckId: deck.id)
                removeDeck(id: deck.id)
                errorMessage = nil
            } catch where ClientFailure.classify(error) == .cancelled {
                return
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    private func upsert(_ deck: LearningDeck, startsPolling: Bool = true) {
        recordMutation(.upsert(deck), for: deck.id)
        if let index = decks.firstIndex(where: { $0.id == deck.id }) {
            decks[index] = deck
        } else {
            decks.insert(deck, at: 0)
        }
        if startsPolling {
            startPollingIfNeeded(deck)
        }
    }

    private func removeDeck(id: Int) {
        recordMutation(.remove, for: id)
        tasks.cancel(.deckPolling(id))
        decks.removeAll { $0.id == id }
    }

    private func recordMutation(_ mutation: LearningDeckMutation, for deckID: Int) {
        mutationRevision &+= 1
        deckMutations[deckID] = VersionedLearningDeckMutation(
            revision: mutationRevision,
            mutation: mutation
        )
    }

    private func reconcileLoadedDecks(
        _ loadedDecks: [LearningDeck],
        requestStartRevision: Int
    ) -> [LearningDeck] {
        var reconciled = loadedDecks
        let loadedDeckIDs = Set(loadedDecks.map(\.id))
        let mutations = deckMutations.sorted { $0.value.revision < $1.value.revision }

        for (deckID, versionedMutation) in mutations {
            switch versionedMutation.mutation {
            case .upsert(let deck):
                if versionedMutation.revision <= requestStartRevision,
                   loadedDeckIDs.contains(deckID) {
                    deckMutations.removeValue(forKey: deckID)
                    continue
                }
                reconciled.removeAll { $0.id == deckID }
                reconciled.insert(deck, at: 0)
            case .remove:
                reconciled.removeAll { $0.id == deckID }
                if versionedMutation.revision <= requestStartRevision,
                   !loadedDeckIDs.contains(deckID) {
                    deckMutations.removeValue(forKey: deckID)
                }
            }
        }
        return reconciled
    }

    private func withDeckBusy<T>(
        _ deckId: Int,
        operation: () async -> T
    ) async -> T {
        busyDeckIDs.insert(deckId)
        defer { busyDeckIDs.remove(deckId) }
        return await operation()
    }

    private func startPollingIfNeeded(_ deck: LearningDeck) {
        let taskKey = LearningDecksTaskKey.deckPolling(deck.id)
        guard deck.hasActiveLatestRun else {
            tasks.cancel(taskKey)
            return
        }

        tasks.runIfIdle(taskKey) { [weak self] in
            await self?.observeUntilLatestRunFinishes(deckId: deck.id)
        }
    }

    private func observeUntilLatestRunFinishes(deckId: Int) async {
        do {
            let latest = try await statusRegistry.waitUntilTerminal(
                deckId: deckId,
                onUpdate: { [weak self] deck in
                    self?.upsert(deck, startsPolling: false)
                    self?.errorMessage = nil
                }
            )
            upsert(latest, startsPolling: false)
            errorMessage = nil
        } catch where ClientFailure.classify(error) == .cancelled {
            return
        } catch LearningDeckStatusRegistryError.timeout {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
