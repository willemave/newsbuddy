//
//  AudioEpisodeService.swift
//  newsly
//

import Foundation

enum AudioEpisodeDelivery: String {
    case background
    case stream
}

final class AudioEpisodeService {
    static let shared = AudioEpisodeService()

    private let client = APIClient.shared

    private init() {}

    func createFastNewsEpisode(
        delivery: AudioEpisodeDelivery = .background
    ) async throws -> AudioEpisode {
        try await client.request(
            APIEndpoints.fastNewsAudioEpisode,
            method: "POST",
            queryItems: [URLQueryItem(name: "delivery", value: delivery.rawValue)]
        )
    }

    func createContentCouncilEpisode(
        contentId: Int,
        delivery: AudioEpisodeDelivery = .background
    ) async throws -> AudioEpisode {
        try await client.request(
            APIEndpoints.contentCouncilAudioEpisode(id: contentId),
            method: "POST",
            queryItems: [URLQueryItem(name: "delivery", value: delivery.rawValue)]
        )
    }

    func fetchEpisode(id: Int) async throws -> AudioEpisode {
        try await client.request(APIEndpoints.audioEpisode(id: id))
    }

    func fetchEpisodeAudio(id: Int) async throws -> Data {
        try await client.requestData(
            APIEndpoints.audioEpisodeAudio(id: id),
            accept: "audio/mpeg"
        )
    }

    func streamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource {
        guard let endpoint = episode.streamUrl ?? episode.audioUrl else {
            throw NSError(
                domain: "AudioEpisodeService",
                code: 3,
                userInfo: [NSLocalizedDescriptionKey: "No audio stream is available."]
            )
        }
        return try await client.authorizedMediaResource(endpoint, accept: "audio/mpeg")
    }

    func waitForCompletedEpisode(
        _ episode: AudioEpisode,
        pollIntervalNanoseconds: UInt64 = 1_500_000_000,
        maxAttempts: Int = 120
    ) async throws -> AudioEpisode {
        var current = episode
        for _ in 0..<maxAttempts {
            if current.status == "completed" {
                return current
            }
            if current.status == "failed" {
                throw NSError(
                    domain: "AudioEpisodeService",
                    code: 1,
                    userInfo: [
                        NSLocalizedDescriptionKey: current.errorMessage
                            ?? "Audio episode generation failed."
                    ]
                )
            }
            try await Task.sleep(nanoseconds: pollIntervalNanoseconds)
            current = try await fetchEpisode(id: current.id)
        }
        throw NSError(
            domain: "AudioEpisodeService",
            code: 2,
            userInfo: [NSLocalizedDescriptionKey: "Audio episode is still preparing."]
        )
    }
}
