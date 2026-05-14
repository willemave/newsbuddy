//
//  AudioEpisodeService.swift
//  newsly
//

import Foundation

final class AudioEpisodeService {
    static let shared = AudioEpisodeService()

    private let client = APIClient.shared

    private init() {}

    func createFastNewsEpisode() async throws -> AudioEpisode {
        try await client.request(
            APIEndpoints.fastNewsAudioEpisode,
            method: "POST"
        )
    }

    func createContentCouncilEpisode(contentId: Int) async throws -> AudioEpisode {
        try await client.request(
            APIEndpoints.contentCouncilAudioEpisode(id: contentId),
            method: "POST"
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
