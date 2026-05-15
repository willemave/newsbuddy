//
//  Narration.swift
//  newsly
//

import Foundation

enum NarrationTarget: Hashable {
    case audioEpisode(Int)
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
