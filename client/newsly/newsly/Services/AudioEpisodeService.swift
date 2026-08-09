//
//  AudioEpisodeService.swift
//  newsly
//

import Foundation
import os.log

private let audioEpisodeLogger = Logger(subsystem: "com.newsly", category: "AudioEpisode")

private func elapsedMilliseconds(since start: Date) -> Int {
    Int(Date().timeIntervalSince(start) * 1000)
}

enum AudioEpisodeDelivery: String {
    case background
    case stream
}

enum AudioEpisodeServiceError: LocalizedError, Equatable {
    case generationFailed
    case preparationTimedOut
    case missingStreamResource

    var userFacingMessage: String {
        switch self {
        case .generationFailed:
            return "Couldn't prepare audio. Please try again."
        case .preparationTimedOut:
            return "Audio is taking longer than expected. Please try again."
        case .missingStreamResource:
            return "No audio stream is available."
        }
    }

    var errorDescription: String? {
        userFacingMessage
    }
}

struct AudioEpisodePoller {
    typealias FetchEpisode = (Int) async throws -> AudioEpisode

    private let fetchEpisode: FetchEpisode

    init(fetchEpisode: @escaping FetchEpisode) {
        self.fetchEpisode = fetchEpisode
    }

    func waitForCompletedEpisode(
        _ episode: AudioEpisode,
        pollIntervalNanoseconds: UInt64,
        maxAttempts: Int
    ) async throws -> AudioEpisode {
        let startedAt = Date()
        var current = episode
        audioEpisodeLogger.info(
            "Wait episode started | episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public) maxAttempts=\(maxAttempts)"
        )
        var attempts = 0
        while true {
            if current.isCompleted {
                audioEpisodeLogger.info(
                    "Wait episode completed | episodeId=\(episode.id) elapsedMs=\(elapsedMilliseconds(since: startedAt))"
                )
                return current
            }
            if current.isFailed {
                audioEpisodeLogger.error(
                    "Wait episode failed | episodeId=\(episode.id) elapsedMs=\(elapsedMilliseconds(since: startedAt)) error=\(current.errorMessage ?? "unknown", privacy: .private)"
                )
                throw AudioEpisodeServiceError.generationFailed
            }
            guard attempts < maxAttempts else { break }
            try await Task.sleep(nanoseconds: pollIntervalNanoseconds)
            current = try await fetchEpisode(current.id)
            attempts += 1
        }
        audioEpisodeLogger.error(
            "Wait episode timed out | episodeId=\(episode.id) elapsedMs=\(elapsedMilliseconds(since: startedAt)) attempts=\(maxAttempts)"
        )
        throw AudioEpisodeServiceError.preparationTimedOut
    }
}

final class AudioEpisodeService {
    static let shared = AudioEpisodeService()

    private let client = APIClient.shared

    private init() {}

    func createFastNewsEpisode(
        delivery: AudioEpisodeDelivery = .background
    ) async throws -> AudioEpisode {
        let startedAt = Date()
        audioEpisodeLogger.info(
            "Create episode started | kind=fast_news_digest delivery=\(delivery.rawValue, privacy: .public)"
        )
        do {
            let episode: AudioEpisode = try await client.request(
                APIEndpoints.fastNewsAudioEpisode,
                method: "POST",
                queryItems: [URLQueryItem(name: "delivery", value: delivery.rawValue)]
            )
            audioEpisodeLogger.info(
                "Create episode completed | kind=fast_news_digest episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public) elapsedMs=\(elapsedMilliseconds(since: startedAt)) hasStream=\(episode.streamUrl != nil)"
            )
            return episode
        } catch {
            audioEpisodeLogger.error(
                "Create episode failed | kind=fast_news_digest elapsedMs=\(elapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .private)"
            )
            throw error
        }
    }

    func createContentCouncilEpisode(
        contentId: Int,
        delivery: AudioEpisodeDelivery = .background
    ) async throws -> AudioEpisode {
        let startedAt = Date()
        audioEpisodeLogger.info(
            "Create episode started | kind=content_council_discussion contentId=\(contentId) delivery=\(delivery.rawValue, privacy: .public)"
        )
        do {
            let episode: AudioEpisode = try await client.request(
                APIEndpoints.contentCouncilAudioEpisode(id: contentId),
                method: "POST",
                queryItems: [URLQueryItem(name: "delivery", value: delivery.rawValue)]
            )
            audioEpisodeLogger.info(
                "Create episode completed | kind=content_council_discussion contentId=\(contentId) episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public) elapsedMs=\(elapsedMilliseconds(since: startedAt)) hasStream=\(episode.streamUrl != nil)"
            )
            return episode
        } catch {
            audioEpisodeLogger.error(
                "Create episode failed | kind=content_council_discussion contentId=\(contentId) elapsedMs=\(elapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .private)"
            )
            throw error
        }
    }

    func createNewsItemDiscussionEpisode(
        newsItemId: Int,
        delivery: AudioEpisodeDelivery = .background
    ) async throws -> AudioEpisode {
        let startedAt = Date()
        audioEpisodeLogger.info(
            "Create episode started | kind=news_item_discussion newsItemId=\(newsItemId) delivery=\(delivery.rawValue, privacy: .public)"
        )
        do {
            let episode: AudioEpisode = try await client.request(
                APIEndpoints.newsItemAudioEpisode(id: newsItemId),
                method: "POST",
                queryItems: [URLQueryItem(name: "delivery", value: delivery.rawValue)]
            )
            audioEpisodeLogger.info(
                "Create episode completed | kind=news_item_discussion newsItemId=\(newsItemId) episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public) elapsedMs=\(elapsedMilliseconds(since: startedAt)) hasStream=\(episode.streamUrl != nil)"
            )
            return episode
        } catch {
            audioEpisodeLogger.error(
                "Create episode failed | kind=news_item_discussion newsItemId=\(newsItemId) elapsedMs=\(elapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .private)"
            )
            throw error
        }
    }

    func createCustomNarrationEpisode(
        contentIds: [Int],
        newsItemIds: [Int] = [],
        title: String? = nil,
        markSourceContentReadOnPlay: Bool = false,
        delivery: AudioEpisodeDelivery = .background
    ) async throws -> AudioEpisode {
        let startedAt = Date()
        let payload = APICustomNarrationCreateRequest(
            contentIds: contentIds,
            newsItemIds: newsItemIds,
            title: title,
            markSourceContentReadOnPlay: markSourceContentReadOnPlay
        )
        let body = try JSONEncoder().encode(payload)
        audioEpisodeLogger.info(
            "Create episode started | kind=custom_narration contentCount=\(contentIds.count) newsItemCount=\(newsItemIds.count) delivery=\(delivery.rawValue, privacy: .public)"
        )
        do {
            let episode: AudioEpisode = try await client.request(
                APIEndpoints.customNarrationAudioEpisodes,
                method: "POST",
                body: body,
                queryItems: [URLQueryItem(name: "delivery", value: delivery.rawValue)]
            )
            audioEpisodeLogger.info(
                "Create episode completed | kind=custom_narration episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public) elapsedMs=\(elapsedMilliseconds(since: startedAt)) sourceCount=\(episode.sourceCount)"
            )
            return episode
        } catch {
            audioEpisodeLogger.error(
                "Create episode failed | kind=custom_narration elapsedMs=\(elapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .private)"
            )
            throw error
        }
    }

    func enableEpisodeShare(id: Int) async throws -> AudioEpisodeShareResponse {
        let startedAt = Date()
        let response: AudioEpisodeShareResponse = try await client.request(
            APIEndpoints.audioEpisodeShare(id: id),
            method: "POST"
        )
        audioEpisodeLogger.info(
            "Enable episode share completed | episodeId=\(id) elapsedMs=\(elapsedMilliseconds(since: startedAt)) hasPageUrl=\(response.sharePageUrl != nil) hasAudioUrl=\(response.shareAudioUrl != nil)"
        )
        return response
    }

    func fetchCustomNarrationEpisodes(limit: Int = 20) async throws -> [AudioEpisode] {
        let startedAt = Date()
        let episodes: [AudioEpisode] = try await client.request(
            APIEndpoints.customNarrationAudioEpisodes,
            queryItems: [URLQueryItem(name: "limit", value: String(limit))]
        )
        audioEpisodeLogger.info(
            "Fetch custom narrations completed | count=\(episodes.count) elapsedMs=\(elapsedMilliseconds(since: startedAt))"
        )
        return episodes
    }

    func fetchEpisode(id: Int) async throws -> AudioEpisode {
        let startedAt = Date()
        let episode: AudioEpisode = try await client.request(APIEndpoints.audioEpisode(id: id))
        audioEpisodeLogger.info(
            "Fetch episode completed | episodeId=\(id) status=\(episode.status.rawValue, privacy: .public) elapsedMs=\(elapsedMilliseconds(since: startedAt))"
        )
        return episode
    }

    func fetchEpisodeAudio(id: Int) async throws -> Data {
        let startedAt = Date()
        let data = try await client.requestData(
            APIEndpoints.audioEpisodeAudio(id: id),
            accept: "audio/mpeg"
        )
        audioEpisodeLogger.info(
            "Fetch episode audio completed | episodeId=\(id) bytes=\(data.count) elapsedMs=\(elapsedMilliseconds(since: startedAt))"
        )
        return data
    }

    func markPlaybackFinished(id: Int) async throws {
        let startedAt = Date()
        try await client.requestVoid(APIEndpoints.audioEpisodePlaybackFinished(id: id))
        audioEpisodeLogger.info(
            "Playback completion recorded | episodeId=\(id) elapsedMs=\(elapsedMilliseconds(since: startedAt))"
        )
    }

    func streamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource {
        let startedAt = Date()
        guard let endpoint = episode.audioUrl ?? episode.streamUrl else {
            audioEpisodeLogger.error(
                "Stream resource missing | episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public)"
            )
            throw AudioEpisodeServiceError.missingStreamResource
        }
        audioEpisodeLogger.info(
            "Stream resource started | episodeId=\(episode.id) endpoint=\(endpoint, privacy: .public)"
        )
        do {
            let resource = try await client.authorizedMediaResource(endpoint, accept: "audio/mpeg")
            audioEpisodeLogger.info(
                "Stream resource ready | episodeId=\(episode.id) elapsedMs=\(elapsedMilliseconds(since: startedAt)) hasAuthHeaders=\(!resource.headers.isEmpty)"
            )
            return resource
        } catch {
            audioEpisodeLogger.error(
                "Stream resource failed | episodeId=\(episode.id) elapsedMs=\(elapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .private)"
            )
            throw error
        }
    }

    func waitForCompletedEpisode(
        _ episode: AudioEpisode,
        pollIntervalNanoseconds: UInt64 = 1_500_000_000,
        maxAttempts: Int = 120
    ) async throws -> AudioEpisode {
        try await AudioEpisodePoller(fetchEpisode: fetchEpisode).waitForCompletedEpisode(
            episode,
            pollIntervalNanoseconds: pollIntervalNanoseconds,
            maxAttempts: maxAttempts
        )
    }
}
