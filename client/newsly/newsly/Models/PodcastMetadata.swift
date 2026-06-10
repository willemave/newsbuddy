//
//  PodcastMetadata.swift
//  newsly
//
//  Created by Assistant on 8/9/25.
//

import Foundation

struct PodcastMetadata: Codable {
    let source: String?
    let audioUrl: String?
    let transcript: String?
    let duration: Int?
    let episodeNumber: Int?
    let videoUrl: String?
    let videoId: String?
    let channelName: String?
    let thumbnailUrl: String?
    let viewCount: Int?
    let likeCount: Int?
    let hasTranscript: Bool?
    let summary: StructuredSummary?
    let summarizationDate: Date?
    let wordCount: Int?
    
    enum CodingKeys: String, CodingKey {
        case source
        case audioUrl = "audio_url"
        case transcript
        case duration
        case episodeNumber = "episode_number"
        case videoUrl = "video_url"
        case videoId = "video_id"
        case channelName = "channel_name"
        case thumbnailUrl = "thumbnail_url"
        case viewCount = "view_count"
        case likeCount = "like_count"
        case hasTranscript = "has_transcript"
        case summary
        case summarizationDate = "summarization_date"
        case wordCount = "word_count"
    }
    
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        
        source = try container.decodeIfPresent(String.self, forKey: .source)
        audioUrl = try container.decodeIfPresent(String.self, forKey: .audioUrl)
        transcript = try container.decodeIfPresent(String.self, forKey: .transcript)
        duration = try container.decodeIfPresent(Int.self, forKey: .duration)
        episodeNumber = try container.decodeIfPresent(Int.self, forKey: .episodeNumber)
        videoUrl = try container.decodeIfPresent(String.self, forKey: .videoUrl)
        videoId = try container.decodeIfPresent(String.self, forKey: .videoId)
        channelName = try container.decodeIfPresent(String.self, forKey: .channelName)
        thumbnailUrl = try container.decodeIfPresent(String.self, forKey: .thumbnailUrl)
        viewCount = try container.decodeIfPresent(Int.self, forKey: .viewCount)
        likeCount = try container.decodeIfPresent(Int.self, forKey: .likeCount)
        hasTranscript = try container.decodeIfPresent(Bool.self, forKey: .hasTranscript)
        summary = try container.decodeIfPresent(StructuredSummary.self, forKey: .summary)
        wordCount = try container.decodeIfPresent(Int.self, forKey: .wordCount)
        
        // Handle date parsing for summarizationDate
        if let dateString = try container.decodeIfPresent(String.self, forKey: .summarizationDate) {
            summarizationDate = ServerDate.parse(dateString)
        } else {
            summarizationDate = nil
        }
    }
    
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        
        try container.encodeIfPresent(source, forKey: .source)
        try container.encodeIfPresent(audioUrl, forKey: .audioUrl)
        try container.encodeIfPresent(transcript, forKey: .transcript)
        try container.encodeIfPresent(duration, forKey: .duration)
        try container.encodeIfPresent(episodeNumber, forKey: .episodeNumber)
        try container.encodeIfPresent(videoUrl, forKey: .videoUrl)
        try container.encodeIfPresent(videoId, forKey: .videoId)
        try container.encodeIfPresent(channelName, forKey: .channelName)
        try container.encodeIfPresent(thumbnailUrl, forKey: .thumbnailUrl)
        try container.encodeIfPresent(viewCount, forKey: .viewCount)
        try container.encodeIfPresent(likeCount, forKey: .likeCount)
        try container.encodeIfPresent(hasTranscript, forKey: .hasTranscript)
        try container.encodeIfPresent(summary, forKey: .summary)
        try container.encodeIfPresent(wordCount, forKey: .wordCount)
        
        // Encode date as ISO8601 string
        if let date = summarizationDate {
            try container.encode(ISO8601DateFormatter().string(from: date), forKey: .summarizationDate)
        }
    }
    
    var formattedDuration: String? {
        guard let duration = duration else { return nil }
        let hours = duration / 3600
        let minutes = (duration % 3600) / 60
        let seconds = duration % 60
        
        if hours > 0 {
            return String(format: "%d:%02d:%02d", hours, minutes, seconds)
        } else {
            return String(format: "%d:%02d", minutes, seconds)
        }
    }
    
    var formattedViewCount: String? {
        guard let viewCount = viewCount else { return nil }
        
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.maximumFractionDigits = 1
        
        if viewCount >= 1_000_000 {
            let millions = Double(viewCount) / 1_000_000
            return "\(formatter.string(from: NSNumber(value: millions)) ?? "0")M views"
        } else if viewCount >= 1_000 {
            let thousands = Double(viewCount) / 1_000
            return "\(formatter.string(from: NSNumber(value: thousands)) ?? "0")K views"
        } else {
            return "\(viewCount) views"
        }
    }
}
