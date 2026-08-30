//
//  PodcastAudioController.swift
//  newsly
//

import Foundation
import Observation
import os.log

private let podcastAudioLogger = Logger(subsystem: "com.newsly", category: "PodcastAudio")

protocol PodcastAudioEpisodeServicing: AnyObject {
    func streamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource
    func createContentCouncilEpisode(contentId: Int, delivery: AudioEpisodeDelivery) async throws -> AudioEpisode
    func createNewsItemDiscussionEpisode(newsItemId: Int, delivery: AudioEpisodeDelivery) async throws -> AudioEpisode
}

extension AudioEpisodeService: PodcastAudioEpisodeServicing {}

@MainActor
@Observable
final class PodcastAudioController {
    let playbackService: NarrationPlaybackService

    private var loadingAudioEpisodeContentIds: Set<Int> = []
    private var audioEpisodeByContentId: [Int: AudioEpisode] = [:]
    private var audioRequestGeneration = 0
    private let audioEpisodeService: any PodcastAudioEpisodeServicing

    init(
        playbackService: NarrationPlaybackService,
        audioEpisodeService: any PodcastAudioEpisodeServicing
    ) {
        self.playbackService = playbackService
        self.audioEpisodeService = audioEpisodeService
    }

    func supportsAudio(for content: ContentDetail) -> Bool {
        content.contentType == .article || content.contentType == .news || content.contentType == .podcast
    }

    func target(for content: ContentDetail) -> NarrationTarget? {
        target(forContentId: content.id)
    }

    func target(forContentId contentId: Int) -> NarrationTarget? {
        guard let episode = audioEpisodeByContentId[contentId] else { return nil }
        return .audioEpisode(episode.id)
    }

    func isActive(for content: ContentDetail) -> Bool {
        guard let target = target(for: content) else { return false }
        return playbackService.isSpeaking
            && playbackService.speakingTarget == target
    }

    func isLoading(for content: ContentDetail) -> Bool {
        loadingAudioEpisodeContentIds.contains(content.id)
    }

    func shouldShowControls(for content: ContentDetail) -> Bool {
        if isLoading(for: content) {
            return true
        }
        guard let target = target(for: content) else { return false }
        return playbackService.speakingTarget == target
    }

    func accessibilityLabel(for content: ContentDetail) -> String {
        if isActive(for: content) {
            return "Pause podcast overview"
        }
        return "Play podcast overview at \(playbackService.playbackSpeedTitle)"
    }

    func statusText(for content: ContentDetail) -> String {
        if isLoading(for: content) {
            return "Preparing audio"
        }
        if isActive(for: content) {
            return "Playing at \(playbackService.playbackSpeedTitle)"
        }
        return "1-minute discussion"
    }

    func stopIfSpeaking(for content: ContentDetail) {
        stopIfSpeaking(forContentId: content.id)
    }

    func stopIfSpeaking(forContentId contentId: Int?) {
        audioRequestGeneration += 1
        guard let contentId,
              let target = target(forContentId: contentId),
              playbackService.speakingTarget == target else {
            return
        }
        playbackService.stop()
    }

    func handleAudio(
        for content: ContentDetail,
        currentContentId: Int?,
        rate: Float? = nil
    ) async throws {
        let startedAt = Date()
        let playbackRate = rate ?? playbackService.playbackRate
        if isActive(for: content) {
            if abs(playbackService.playbackRate - playbackRate) < 0.001 {
                playbackService.pause()
            } else {
                playbackService.setPlaybackRate(playbackRate)
            }
            return
        }
        guard supportsAudio(for: content) else { return }
        guard !isLoading(for: content) else { return }
        audioRequestGeneration += 1
        let requestGeneration = audioRequestGeneration
        podcastAudioLogger.info(
            "Flow started | contentId=\(content.id) type=\(content.contentType.rawValue, privacy: .public) rate=\(playbackRate)"
        )

        loadingAudioEpisodeContentIds.insert(content.id)
        defer { loadingAudioEpisodeContentIds.remove(content.id) }

        do {
            let episode: AudioEpisode
            if let existingEpisode = audioEpisodeByContentId[content.id] {
                episode = existingEpisode
                podcastAudioLogger.info(
                    "Reusing episode | contentId=\(content.id) episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public)"
                )
            } else {
                episode = try await createAudioEpisode(for: content)
                guard requestGeneration == audioRequestGeneration else { return }
                podcastAudioLogger.info(
                    "Episode created | contentId=\(content.id) episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public) elapsedMs=\(self.elapsedMilliseconds(since: startedAt))"
                )
            }
            audioEpisodeByContentId[content.id] = episode
            guard requestGeneration == audioRequestGeneration,
                  currentContentId == content.id else { return }
            let target = NarrationTarget.audioEpisode(episode.id)
            try await playbackService.playStreamingNarration(
                for: target,
                rate: playbackRate,
                fetchStreamResource: {
                    let resource = try await self.audioEpisodeService.streamResource(for: episode)
                    guard requestGeneration == self.audioRequestGeneration else {
                        throw CancellationError()
                    }
                    return resource
                }
            )
            guard requestGeneration == audioRequestGeneration else {
                if playbackService.speakingTarget == target {
                    playbackService.stop()
                }
                return
            }
            podcastAudioLogger.info(
                "Playback requested | contentId=\(content.id) episodeId=\(episode.id) elapsedMs=\(self.elapsedMilliseconds(since: startedAt))"
            )
        } catch where ClientFailure.classify(error) == .cancelled {
            return
        } catch {
            podcastAudioLogger.error(
                "Flow failed | contentId=\(content.id) elapsedMs=\(self.elapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            throw error
        }
    }

    private func createAudioEpisode(for content: ContentDetail) async throws -> AudioEpisode {
        switch content.contentType {
        case .article, .podcast, .insight_report, .unknown, .unknownRaw:
            return try await audioEpisodeService.createContentCouncilEpisode(
                contentId: content.id,
                delivery: .stream
            )
        case .news:
            return try await audioEpisodeService.createNewsItemDiscussionEpisode(
                newsItemId: content.id,
                delivery: .stream
            )
        }
    }

    private func elapsedMilliseconds(since start: Date) -> Int {
        Int(Date().timeIntervalSince(start) * 1000)
    }
}
