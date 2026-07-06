//
//  LongFormAudioController.swift
//  newsly
//

import Foundation
import Observation
import os.log

private let longFormAudioLogger = Logger(subsystem: "com.newsly", category: "LongFormAudio")

@MainActor
@Observable
final class LongFormAudioController {
    let playbackService = NarrationPlaybackService.shared
    private var loadingContentIds: Set<Int> = []
    private var episodeByContentId: [Int: AudioEpisode] = [:]
    private var errorByContentId: [Int: String] = [:]

    func supportsAudioDiscussion(for content: ContentSummary) -> Bool {
        content.contentType == .article || content.contentType == .podcast
    }

    func audioTarget(for content: ContentSummary) -> NarrationTarget? {
        guard let episode = episodeByContentId[content.id] else { return nil }
        return .audioEpisode(episode.id)
    }

    func isAudioCurrent(for content: ContentSummary) -> Bool {
        guard let target = audioTarget(for: content) else { return false }
        return playbackService.speakingTarget == target
    }

    func isAudioPlaying(for content: ContentSummary) -> Bool {
        isAudioCurrent(for: content) && playbackService.isSpeaking
    }

    func isAudioPreparing(for content: ContentSummary) -> Bool {
        loadingContentIds.contains(content.id)
    }

    func shouldShowAudioControls(for content: ContentSummary) -> Bool {
        isAudioPreparing(for: content) || isAudioCurrent(for: content)
    }

    func errorMessage(for content: ContentSummary) -> String? {
        errorByContentId[content.id]
    }

    func handleAudioDiscussion(for content: ContentSummary) {
        Task { @MainActor in
            await handleAudioDiscussionTask(for: content)
        }
    }

    private func handleAudioDiscussionTask(for content: ContentSummary) async {
        if isAudioPlaying(for: content) {
            playbackService.pause()
            return
        }
        guard supportsAudioDiscussion(for: content) else { return }
        guard !isAudioPreparing(for: content) else { return }
        let startedAt = Date()
        longFormAudioLogger.info(
            "Long-form audio flow started | contentId=\(content.id) type=\(content.contentType.rawValue, privacy: .public)"
        )

        if isAudioCurrent(for: content),
           let episode = episodeByContentId[content.id] {
            await playAudioDiscussionEpisode(episode, contentId: content.id)
            longFormAudioLogger.info(
                "Long-form audio resumed existing episode | contentId=\(content.id) episodeId=\(episode.id) elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
            )
            return
        }

        loadingContentIds.insert(content.id)
        errorByContentId[content.id] = nil
        defer { loadingContentIds.remove(content.id) }

        do {
            let episode: AudioEpisode
            if let existingEpisode = episodeByContentId[content.id] {
                episode = existingEpisode
                longFormAudioLogger.info(
                    "Long-form audio reusing episode | contentId=\(content.id) episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public)"
                )
            } else {
                episode = try await AudioEpisodeService.shared.createContentCouncilEpisode(
                    contentId: content.id,
                    delivery: .inline
                )
                longFormAudioLogger.info(
                    "Long-form audio episode created | contentId=\(content.id) episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public) elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
                )
            }
            episodeByContentId[content.id] = episode
            try await playAudioDiscussionEpisode(episode)
            longFormAudioLogger.info(
                "Long-form audio playback requested | contentId=\(content.id) episodeId=\(episode.id) elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
            )
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            longFormAudioLogger.error(
                "Long-form audio flow failed | contentId=\(content.id) elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) error=\(error.localizedDescription, privacy: .public)"
            )
            errorByContentId[content.id] = error.localizedDescription
        }
    }

    private func playAudioDiscussionEpisode(_ episode: AudioEpisode, contentId: Int) async {
        do {
            episodeByContentId[contentId] = episode
            try await playAudioDiscussionEpisode(episode)
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            errorByContentId[contentId] = error.localizedDescription
        }
    }

    private func playAudioDiscussionEpisode(_ episode: AudioEpisode) async throws {
        let target = NarrationTarget.audioEpisode(episode.id)
        try await playbackService.playStreamingNarration(
            for: target,
            fetchStreamResource: {
                try await AudioEpisodeService.shared.streamResource(for: episode)
            }
        )
    }
}
