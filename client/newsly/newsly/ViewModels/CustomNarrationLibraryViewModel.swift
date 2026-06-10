//
//  CustomNarrationLibraryViewModel.swift
//  newsly
//

import Foundation

@MainActor
final class CustomNarrationLibraryViewModel: ObservableObject {
    @Published private(set) var episodes: [AudioEpisode] = []
    @Published private(set) var isLoading = false
    @Published private(set) var sharingEpisodeIds: Set<Int> = []
    @Published var errorMessage: String?

    let playbackService = NarrationPlaybackService.shared
    private var readNotifiedEpisodeIds: Set<Int> = []

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }

        do {
            episodes = try await AudioEpisodeService.shared.fetchCustomNarrationEpisodes()
            errorMessage = nil
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
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
                    try await AudioEpisodeService.shared.streamResource(for: episode)
                }
            )
            await markReadSourcesLocallyIfNeeded(episode)
        } catch where isNetworkCancellation(error) {
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
            let response = try await AudioEpisodeService.shared.enableEpisodeShare(id: episode.id)
            errorMessage = nil
            return response
        } catch where isNetworkCancellation(error) {
            return nil
        } catch {
            errorMessage = error.localizedDescription
            ToastService.shared.showError("Failed to share narration: \(error.localizedDescription)")
            return nil
        }
    }

    private func refresh(_ episode: AudioEpisode) async {
        do {
            let latest = try await AudioEpisodeService.shared.fetchEpisode(id: episode.id)
            replace(latest)
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func replace(_ episode: AudioEpisode) {
        if let index = episodes.firstIndex(where: { $0.id == episode.id }) {
            episodes[index] = episode
        }
    }

    private func markReadSourcesLocallyIfNeeded(_ episode: AudioEpisode) async {
        guard !readNotifiedEpisodeIds.contains(episode.id) else { return }
        let contentIds = episode.readOnPlayContentIds
        let newsItemIds = episode.readOnPlayNewsItemIds
        guard !contentIds.isEmpty || !newsItemIds.isEmpty else { return }

        readNotifiedEpisodeIds.insert(episode.id)
        for contentId in contentIds {
            postReadNotification(contentId: contentId, contentType: .article)
        }
        for newsItemId in newsItemIds {
            postReadNotification(contentId: newsItemId, contentType: .news)
        }
        await UnreadCountService.shared.refreshCounts()
    }

    private func postReadNotification(contentId: Int, contentType: APIContentType) {
        NotificationCenter.default.post(
            name: .contentMarkedAsRead,
            object: nil,
            userInfo: ["contentId": contentId, "contentType": contentType.rawValue]
        )
    }

    private func formattedNarrationDuration(_ seconds: Int) -> String {
        let minutes = max(Int(round(Double(seconds) / 60.0)), 1)
        return "\(minutes) min"
    }
}
