//
//  CustomNarrationLibraryViewModel.swift
//  newsly
//

import Foundation

@MainActor
final class CustomNarrationLibraryViewModel: ObservableObject {
    @Published private(set) var episodes: [AudioEpisode] = []
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    let playbackService = NarrationPlaybackService.shared

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
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            errorMessage = error.localizedDescription
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

    private func formattedNarrationDuration(_ seconds: Int) -> String {
        let minutes = max(Int(round(Double(seconds) / 60.0)), 1)
        return "\(minutes) min"
    }
}
