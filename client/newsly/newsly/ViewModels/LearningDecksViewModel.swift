//
//  LearningDecksViewModel.swift
//  newsly
//

import Foundation

@MainActor
final class LearningDecksViewModel: ObservableObject {
    @Published private(set) var decks: [LearningDeck] = []
    @Published private(set) var isLoading = false
    @Published private(set) var isCreating = false
    @Published private(set) var busyDeckIDs: Set<Int> = []
    @Published var errorMessage: String?

    private let service: LearningDeckService
    private var pollingDeckIDs: Set<Int> = []
    private let pollingIntervalNanoseconds: UInt64 = 3_000_000_000
    private let pollingAttemptLimit = 120

    init(service: LearningDeckService = .shared) {
        self.service = service
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

    func delete(_ deck: LearningDeck) async {
        await withDeckBusy(deck.id) {
            do {
                try await service.deleteDeck(deckId: deck.id)
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
        decks.removeAll { $0.id == deck.id }
        decks.insert(deck, at: 0)
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
        guard deck.hasActiveLatestRun, !pollingDeckIDs.contains(deck.id) else { return }
        pollingDeckIDs.insert(deck.id)

        Task { [weak self] in
            await self?.pollUntilLatestRunFinishes(deckId: deck.id)
        }
    }

    private func pollUntilLatestRunFinishes(deckId: Int) async {
        defer { pollingDeckIDs.remove(deckId) }

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
                errorMessage = error.localizedDescription
                return
            }
        }
    }
}
