//
//  ArticleMetadata.swift
//  newsly
//
//  Created by Assistant on 8/9/25.
//

import Foundation

struct ArticleMetadata: Codable {
    let source: String?
    let content: String?
    let fullMarkdown: String?
    let author: String?
    let publicationDate: Date?
    let contentType: String?
    let finalUrlAfterRedirects: String?
    let summary: StructuredSummary?
    let summarizationDate: Date?
    let wordCount: Int?
    let sourceMetadata: SourceMetadata?
    
    enum CodingKeys: String, CodingKey {
        case source
        case content
        case fullMarkdown = "full_markdown"
        case author
        case publicationDate = "publication_date"
        case contentType = "content_type"
        case finalUrlAfterRedirects = "final_url_after_redirects"
        case summary
        case summarizationDate = "summarization_date"
        case wordCount = "word_count"
        case sourceMetadata = "source_metadata"
    }
    
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        
        source = try container.decodeIfPresent(String.self, forKey: .source)
        content = try container.decodeIfPresent(String.self, forKey: .content)
        fullMarkdown = try container.decodeIfPresent(String.self, forKey: .fullMarkdown)
        author = try container.decodeIfPresent(String.self, forKey: .author)
        contentType = try container.decodeIfPresent(String.self, forKey: .contentType)
        finalUrlAfterRedirects = try container.decodeIfPresent(String.self, forKey: .finalUrlAfterRedirects)
        summary = try container.decodeIfPresent(StructuredSummary.self, forKey: .summary)
        wordCount = try container.decodeIfPresent(Int.self, forKey: .wordCount)
        sourceMetadata = try container.decodeIfPresent(SourceMetadata.self, forKey: .sourceMetadata)
        
        // Handle date parsing for publicationDate
        if let dateString = try container.decodeIfPresent(String.self, forKey: .publicationDate) {
            publicationDate = DateParser.parse(dateString)
        } else {
            publicationDate = nil
        }
        
        // Handle date parsing for summarizationDate
        if let dateString = try container.decodeIfPresent(String.self, forKey: .summarizationDate) {
            summarizationDate = DateParser.parse(dateString)
        } else {
            summarizationDate = nil
        }
    }
    
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        
        try container.encodeIfPresent(source, forKey: .source)
        try container.encodeIfPresent(content, forKey: .content)
        try container.encodeIfPresent(fullMarkdown, forKey: .fullMarkdown)
        try container.encodeIfPresent(author, forKey: .author)
        try container.encodeIfPresent(contentType, forKey: .contentType)
        try container.encodeIfPresent(finalUrlAfterRedirects, forKey: .finalUrlAfterRedirects)
        try container.encodeIfPresent(summary, forKey: .summary)
        try container.encodeIfPresent(wordCount, forKey: .wordCount)
        try container.encodeIfPresent(sourceMetadata, forKey: .sourceMetadata)
        
        // Encode dates as ISO8601 strings
        if let date = publicationDate {
            try container.encode(ISO8601DateFormatter().string(from: date), forKey: .publicationDate)
        }
        if let date = summarizationDate {
            try container.encode(ISO8601DateFormatter().string(from: date), forKey: .summarizationDate)
        }
    }
}

// Helper for parsing dates from various formats
struct DateParser {
    static func parse(_ dateString: String) -> Date? {
        // Try ISO8601 with fractional seconds
        let iso8601WithFractional = ISO8601DateFormatter()
        iso8601WithFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso8601WithFractional.date(from: dateString) {
            return date
        }
        
        // Try ISO8601 without fractional seconds
        let iso8601 = ISO8601DateFormatter()
        iso8601.formatOptions = [.withInternetDateTime]
        if let date = iso8601.date(from: dateString) {
            return date
        }
        
        // Try basic ISO format with microseconds
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
        formatter.timeZone = TimeZone(abbreviation: "UTC")
        if let date = formatter.date(from: dateString) {
            return date
        }
        
        // Try without microseconds
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        if let date = formatter.date(from: dateString) {
            return date
        }
        
        return nil
    }
}
