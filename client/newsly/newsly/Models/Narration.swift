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
    let kind: String
    let status: String
    let title: String
    let sourceContentIds: [Int]
    let sourceCount: Int
    let sourceTitles: [String]
    let durationSeconds: Int?
    let audioUrl: String?
    let streamUrl: String?
    let scriptText: String?
    let errorMessage: String?

    enum CodingKeys: String, CodingKey {
        case id
        case kind
        case status
        case title
        case sourceContentIds = "source_content_ids"
        case sourceCount = "source_count"
        case sourceTitles = "source_titles"
        case durationSeconds = "duration_seconds"
        case audioUrl = "audio_url"
        case streamUrl = "stream_url"
        case scriptText = "script_text"
        case errorMessage = "error_message"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        kind = try container.decodeIfPresent(String.self, forKey: .kind) ?? "unknown"
        status = try container.decode(String.self, forKey: .status)
        title = try container.decode(String.self, forKey: .title)
        sourceContentIds = try container.decodeIfPresent([Int].self, forKey: .sourceContentIds) ?? []
        sourceCount = try container.decodeIfPresent(Int.self, forKey: .sourceCount) ?? sourceContentIds.count
        sourceTitles = try container.decodeIfPresent([String].self, forKey: .sourceTitles) ?? []
        durationSeconds = try container.decodeIfPresent(Int.self, forKey: .durationSeconds)
        audioUrl = try container.decodeIfPresent(String.self, forKey: .audioUrl)
        streamUrl = try container.decodeIfPresent(String.self, forKey: .streamUrl)
        scriptText = try container.decodeIfPresent(String.self, forKey: .scriptText)
        errorMessage = try container.decodeIfPresent(String.self, forKey: .errorMessage)
    }
}

extension AudioEpisode {
    var isGenerating: Bool {
        status == "pending" || status == "processing"
    }

    var isCompleted: Bool {
        status == "completed"
    }

    var isFailed: Bool {
        status == "failed"
    }
}
