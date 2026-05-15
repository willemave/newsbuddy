//
//  Narration.swift
//  newsly
//

import Foundation

enum NarrationTarget: Hashable {
    case content(Int)
    case audioEpisode(Int)

    var id: Int {
        switch self {
        case .content(let id):
            return id
        case .audioEpisode(let id):
            return id
        }
    }

    var pathComponent: String {
        switch self {
        case .content:
            return "content"
        case .audioEpisode:
            return "audio_episode"
        }
    }
}

struct NarrationResponse: Codable {
    let targetType: String
    let targetId: Int
    let title: String
    let narrationText: String

    enum CodingKeys: String, CodingKey {
        case targetType = "target_type"
        case targetId = "target_id"
        case title
        case narrationText = "narration_text"
    }
}

struct AudioEpisode: Codable, Identifiable, Hashable {
    let id: Int
    let status: String
    let title: String
    let durationSeconds: Int?
    let audioUrl: String?
    let streamUrl: String?
    let scriptText: String?
    let errorMessage: String?

    enum CodingKeys: String, CodingKey {
        case id
        case status
        case title
        case durationSeconds = "duration_seconds"
        case audioUrl = "audio_url"
        case streamUrl = "stream_url"
        case scriptText = "script_text"
        case errorMessage = "error_message"
    }
}
