//
//  CustomNarrationLibraryViewModel.swift
//  newsly
//

import Foundation
import Observation

private enum CustomNarrationTaskKey: Hashable {
    case episodePolling(Int)
}

private enum CustomNarrationMutation {
    case upsert(AudioEpisode)
    case remove
}

private struct VersionedCustomNarrationMutation {
    let revision: Int
    let mutation: CustomNarrationMutation
}

@MainActor
protocol CustomNarrationLibraryServicing: AnyObject {
    func createCustomNarrationEpisode(
        contentIds: [Int],
        newsItemIds: [Int],
        title: String?,
        markSourceContentReadOnPlay: Bool,
        delivery: AudioEpisodeDelivery
    ) async throws -> AudioEpisode
    func fetchEpisode(id: Int) async throws -> AudioEpisode
    func fetchCustomNarrationEpisodes(limit: Int) async throws -> [AudioEpisode]
    func streamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource
    func enableEpisodeShare(id: Int) async throws -> AudioEpisodeShareResponse
}

extension AudioEpisodeService: CustomNarrationLibraryServicing {}

@MainActor
@Observable
final class CustomNarrationLibraryViewModel {
    private(set) var episodes: [AudioEpisode] = []
    private(set) var isLoading = false
    private(set) var sharingEpisodeIds: Set<Int> = []
    private(set) var loadErrorMessage: String?
    var errorMessage: String?

    @ObservationIgnored
    let playbackService: NarrationPlaybackService
    @ObservationIgnored
    private let audioService: any CustomNarrationLibraryServicing
    @ObservationIgnored
    private let badgeStatsStore: BadgeStatsStore
    @ObservationIgnored
    private let toastPresenter: any ToastPresenting
    @ObservationIgnored
    private let readStateCache: ReadStateCache
    @ObservationIgnored
    private var readNotifiedEpisodeIds: Set<Int> = []
    @ObservationIgnored
    private let tasks = TaskBag<CustomNarrationTaskKey>()
    @ObservationIgnored
    private let pollingIntervalNanoseconds: UInt64
    @ObservationIgnored
    private let pollingAttemptLimit: Int
    @ObservationIgnored
    private var activeLoadRequestID: UUID?
    @ObservationIgnored
    private var mutationRevision = 0
    @ObservationIgnored
    private var episodeMutations: [Int: VersionedCustomNarrationMutation] = [:]

    init(
        playbackService: NarrationPlaybackService,
        audioService: any CustomNarrationLibraryServicing,
        badgeStatsStore: BadgeStatsStore,
        toastPresenter: any ToastPresenting,
        readStateCache: ReadStateCache? = nil,
        pollingIntervalNanoseconds: UInt64 = 3_000_000_000,
        pollingAttemptLimit: Int = 120
    ) {
        self.playbackService = playbackService
        self.audioService = audioService
        self.badgeStatsStore = badgeStatsStore
        self.toastPresenter = toastPresenter
        self.readStateCache = readStateCache ?? ReadStateCache()
        self.pollingIntervalNanoseconds = pollingIntervalNanoseconds
        self.pollingAttemptLimit = pollingAttemptLimit
    }

    deinit {
        tasks.cancelAll()
    }

    func load() async {
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
            let loadedEpisodes = try await audioService.fetchCustomNarrationEpisodes(limit: 20)
            guard activeLoadRequestID == requestID, !Task.isCancelled else { return }
            episodes = reconcileLoadedEpisodes(
                loadedEpisodes,
                requestStartRevision: requestStartRevision
            )
            episodes.forEach(startPollingIfNeeded)
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
        episodes.forEach(startPollingIfNeeded)
    }

    func clearError() {
        errorMessage = nil
    }

    func isPlaying(_ episode: AudioEpisode) -> Bool {
        playbackService.isSpeaking
            && playbackService.speakingTarget == .audioEpisode(episode.id)
    }

    func isSharing(_ episode: AudioEpisode) -> Bool {
        sharingEpisodeIds.contains(episode.id)
    }

    func subtitle(for episode: AudioEpisode) -> String {
        let sourceText = episode.sourceCount == 1 ? "1 source" : "\(episode.sourceCount) sources"

        if episode.isGenerating {
            return "\(sourceText) • Generating"
        }
        if episode.isFailed {
            return "\(sourceText) • \(episode.errorMessage ?? "Failed")"
        }
        if let duration = episode.durationSeconds {
            return "\(sourceText) • \(formattedNarrationDuration(duration))"
        }
        return sourceText
    }

    func handleTap(_ episode: AudioEpisode) async {
        if isPlaying(episode) {
            playbackService.pause()
            return
        }

        if episode.isGenerating {
            await refresh(episode)
            return
        }

        guard episode.isCompleted else { return }

        do {
            try await playbackService.playStreamingNarration(
                for: .audioEpisode(episode.id),
                fetchStreamResource: {
                    try await self.audioService.streamResource(for: episode)
                }
            )
            await markReadSourcesLocallyIfNeeded(episode)
        } catch where ClientFailure.classify(error) == .cancelled {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func retry(_ episode: AudioEpisode) async {
        guard episode.isFailed,
              !episode.sourceContentIds.isEmpty || !episode.sourceItemIds.isEmpty
        else { return }

        do {
            let replacement = try await audioService.createCustomNarrationEpisode(
                contentIds: episode.sourceContentIds,
                newsItemIds: episode.sourceItemIds,
                title: episode.title,
                markSourceContentReadOnPlay: !episode.readOnPlayContentIds.isEmpty
                    || !episode.readOnPlayNewsItemIds.isEmpty,
                delivery: .background
            )
            removeEpisode(id: episode.id)
            upsert(replacement)
            errorMessage = nil
        } catch where ClientFailure.classify(error) == .cancelled {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func shareLinks(for episode: AudioEpisode) async -> AudioEpisodeShareResponse? {
        guard episode.isCompleted, !sharingEpisodeIds.contains(episode.id) else { return nil }
        sharingEpisodeIds.insert(episode.id)
        defer { sharingEpisodeIds.remove(episode.id) }

        do {
            let response = try await audioService.enableEpisodeShare(id: episode.id)
            errorMessage = nil
            return response
        } catch where ClientFailure.classify(error) == .cancelled {
            return nil
        } catch {
            errorMessage = error.localizedDescription
            toastPresenter.showError("Failed to share narration: \(error.localizedDescription)")
            return nil
        }
    }

    private func refresh(_ episode: AudioEpisode) async {
        do {
            let latest = try await audioService.fetchEpisode(id: episode.id)
            upsert(latest)
        } catch where ClientFailure.classify(error) == .cancelled {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func upsert(
        _ episode: AudioEpisode,
        startsPolling: Bool = true
    ) {
        recordMutation(.upsert(episode), for: episode.id)
        if let index = episodes.firstIndex(where: { $0.id == episode.id }) {
            episodes[index] = episode
        } else {
            episodes.insert(episode, at: 0)
        }

        if startsPolling {
            startPollingIfNeeded(episode)
        }
    }

    private func removeEpisode(id: Int) {
        recordMutation(.remove, for: id)
        tasks.cancel(.episodePolling(id))
        episodes.removeAll { $0.id == id }
    }

    private func recordMutation(_ mutation: CustomNarrationMutation, for episodeID: Int) {
        mutationRevision &+= 1
        episodeMutations[episodeID] = VersionedCustomNarrationMutation(
            revision: mutationRevision,
            mutation: mutation
        )
    }

    private func reconcileLoadedEpisodes(
        _ loadedEpisodes: [AudioEpisode],
        requestStartRevision: Int
    ) -> [AudioEpisode] {
        var reconciled = loadedEpisodes

        for current in episodes where !current.isGenerating {
            guard let index = reconciled.firstIndex(where: { $0.id == current.id }),
                  reconciled[index].isGenerating else {
                continue
            }
            reconciled[index] = current
        }

        let loadedEpisodeIDs = Set(loadedEpisodes.map(\.id))
        let mutations = episodeMutations.sorted { $0.value.revision < $1.value.revision }
        for (episodeID, versionedMutation) in mutations {
            switch versionedMutation.mutation {
            case .upsert(let episode):
                if versionedMutation.revision <= requestStartRevision,
                   loadedEpisodeIDs.contains(episodeID) {
                    episodeMutations.removeValue(forKey: episodeID)
                    continue
                }
                reconciled.removeAll { $0.id == episodeID }
                reconciled.insert(episode, at: 0)
            case .remove:
                reconciled.removeAll { $0.id == episodeID }
                if versionedMutation.revision <= requestStartRevision,
                   !loadedEpisodeIDs.contains(episodeID) {
                    episodeMutations.removeValue(forKey: episodeID)
                }
            }
        }
        return reconciled
    }

    private func startPollingIfNeeded(_ episode: AudioEpisode) {
        let key = CustomNarrationTaskKey.episodePolling(episode.id)
        guard episode.isGenerating else {
            tasks.cancel(key)
            return
        }

        tasks.runIfIdle(key) { [weak self] in
            await self?.pollUntilTerminal(startingWith: episode)
        }
    }

    private func pollUntilTerminal(startingWith episode: AudioEpisode) async {
        var current = episode
        var lastFetchError: Error?

        for _ in 0..<pollingAttemptLimit {
            guard !Task.isCancelled, current.isGenerating else { return }
            do {
                try await Task.sleep(nanoseconds: pollingIntervalNanoseconds)
                guard !Task.isCancelled else { return }
                current = try await audioService.fetchEpisode(id: current.id)
                guard !Task.isCancelled else { return }
                upsert(current, startsPolling: false)
                lastFetchError = nil
            } catch where ClientFailure.classify(error) == .cancelled {
                return
            } catch {
                lastFetchError = error
            }
        }

        guard !Task.isCancelled, current.isGenerating else { return }
        errorMessage = lastFetchError?.localizedDescription
            ?? AudioEpisodeServiceError.preparationTimedOut.userFacingMessage
    }

    private func markReadSourcesLocallyIfNeeded(_ episode: AudioEpisode) async {
        guard !readNotifiedEpisodeIds.contains(episode.id) else { return }
        let contentIds = episode.readOnPlayContentIds
        let newsItemIds = episode.readOnPlayNewsItemIds
        guard !contentIds.isEmpty || !newsItemIds.isEmpty else { return }

        readNotifiedEpisodeIds.insert(episode.id)
        let readKeys = Set(
            contentIds.map { ReadStateKey(id: $0, contentType: .article) }
                + newsItemIds.map { ReadStateKey(id: $0, contentType: .news) }
        )
        readStateCache.markReadLocally(readKeys, adjustUnreadCounts: false)
        await badgeStatsStore.refreshStats()
    }

    private func formattedNarrationDuration(_ seconds: Int) -> String {
        let minutes = max(Int(round(Double(seconds) / 60.0)), 1)
        return "\(minutes) min"
    }
}
