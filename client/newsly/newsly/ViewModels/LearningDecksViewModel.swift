//
//  LearningDecksViewModel.swift
//  newsly
//

import Foundation
import Observation

private enum LearningDecksTaskKey: Hashable {
    case deckPolling(Int)
}

@MainActor
@Observable
final class LearningDecksViewModel {
    private(set) var decks: [LearningDeck] = []
    private(set) var isLoading = false
    private(set) var isCreating = false
    private(set) var busyDeckIDs: Set<Int> = []
    var errorMessage: String?

    @ObservationIgnored
    private let service: any LearningDeckServicing
    @ObservationIgnored
    private let tasks = TaskBag<LearningDecksTaskKey>()
    @ObservationIgnored
    private let pollingIntervalNanoseconds: UInt64
    @ObservationIgnored
    private let pollingAttemptLimit: Int

    init(
        service: any LearningDeckServicing,
        pollingIntervalNanoseconds: UInt64 = 3_000_000_000,
        pollingAttemptLimit: Int = 120
    ) {
        self.service = service
        self.pollingIntervalNanoseconds = pollingIntervalNanoseconds
        self.pollingAttemptLimit = pollingAttemptLimit
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }

        do {
            let response = try await service.listDecks()
            decks = response.decks
            response.decks.forEach(startPollingIfNeeded)
            errorMessage = nil
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
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
        } catch where isNetworkCancellation(error) {
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
            } catch where isNetworkCancellation(error) {
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
            } catch where isNetworkCancellation(error) {
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
            } catch where isNetworkCancellation(error) {
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
                tasks.cancel(.deckPolling(deck.id))
                upsert(replacement)
                errorMessage = nil
                return replacement
            } catch where isNetworkCancellation(error) {
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
                tasks.cancel(.deckPolling(deck.id))
                decks.removeAll { $0.id == deck.id }
                errorMessage = nil
            } catch where isNetworkCancellation(error) {
                return
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    private func upsert(_ deck: LearningDeck) {
        if let index = decks.firstIndex(where: { $0.id == deck.id }) {
            decks[index] = deck
        } else {
            decks.insert(deck, at: 0)
        }
        startPollingIfNeeded(deck)
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
            await self?.pollUntilLatestRunFinishes(deckId: deck.id)
        }
    }

    private func pollUntilLatestRunFinishes(deckId: Int) async {
        for _ in 0..<pollingAttemptLimit {
            do {
                try await Task.sleep(nanoseconds: pollingIntervalNanoseconds)
            } catch {
                return
            }

            guard !Task.isCancelled else { return }

            do {
                let latest = try await service.fetchDeck(id: deckId)
                upsert(latest)
                errorMessage = nil
                if !latest.hasActiveLatestRun {
                    return
                }
            } catch where isNetworkCancellation(error) {
                return
            } catch {
                continue
            }
        }
    }
}
