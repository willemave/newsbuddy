//
//  StructuredSummary.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation

struct StructuredSummary: Codable {
    let title: String?
    let overview: String?
    let bulletPoints: [BulletPoint]
    let quotes: [Quote]
    let topics: [String]
    let questions: [String]?
    let counterArguments: [String]?
    let summarizationDate: String?
    let classification: String?

    enum CodingKeys: String, CodingKey {
        case title
        case overview
        case bulletPoints = "bullet_points"
        case quotes
        case topics
        case questions
        case counterArguments = "counter_arguments"
        case summarizationDate = "summarization_date"
        case classification
    }
}

struct BulletPoint: Codable {
    let text: String
    let category: String?
}

struct Quote: Codable {
    let text: String
    let context: String?
    let attribution: String?

    enum CodingKeys: String, CodingKey {
        case text
        case context
        case attribution
    }
}

// MARK: - Interleaved Summary Format

struct InterleavedInsight: Codable, Identifiable {
    let topic: String
    let insight: String
    let supportingQuote: String?
    let quoteAttribution: String?

    var id: String { topic + insight.prefix(20) }

    enum CodingKeys: String, CodingKey {
        case topic
        case insight
        case supportingQuote = "supporting_quote"
        case quoteAttribution = "quote_attribution"
    }
}

struct InterleavedSummary: Codable {
    let summaryType: String?
    let title: String?
    let hook: String
    let insights: [InterleavedInsight]
    let takeaway: String
    let classification: String?
    let summarizationDate: String?

    enum CodingKeys: String, CodingKey {
        case summaryType = "summary_type"
        case title
        case hook
        case insights
        case takeaway
        case classification
        case summarizationDate = "summarization_date"
    }
}

// MARK: - Interleaved Summary v2

struct InterleavedTopic: Codable, Identifiable {
    let topic: String
    let bullets: [BulletPoint]

    var id: String { topic }
}

struct InterleavedSummaryV2: Codable {
    let title: String?
    let hook: String
    let keyPoints: [BulletPoint]
    let topics: [InterleavedTopic]
    let quotes: [Quote]
    let takeaway: String
    let classification: String?
    let summarizationDate: String?

    enum CodingKeys: String, CodingKey {
        case title
        case hook
        case keyPoints = "key_points"
        case topics
        case quotes
        case takeaway
        case classification
        case summarizationDate = "summarization_date"
    }
}

// MARK: - Bulleted Summary v1

struct BulletSummaryPoint: Codable, Identifiable {
    let text: String
    let detail: String
    let quotes: [Quote]

    var id: String { text }
}

struct BulletedSummary: Codable {
    let title: String?
    let points: [BulletSummaryPoint]
    let classification: String?
    let summarizationDate: String?

    enum CodingKeys: String, CodingKey {
        case title
        case points
        case classification
        case summarizationDate = "summarization_date"
    }
}

// MARK: - Editorial Narrative Summary v1

struct EditorialKeyPoint: Codable, Identifiable {
    let point: String

    var id: String { point }
}

struct EditorialArchetypeReaction: Codable, Identifiable {
    let archetype: String
    let paragraphs: [String]

    var id: String { archetype }

    var displayParagraphs: [String] {
        paragraphs
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }
}

struct EditorialNarrativeSummary: Codable {
    let title: String?
    let editorialNarrative: String
    let quotes: [Quote]
    let archetypeReactions: [EditorialArchetypeReaction]?
    let keyPoints: [EditorialKeyPoint]
    let classification: String?
    let summarizationDate: String?

    enum CodingKeys: String, CodingKey {
        case title
        case editorialNarrative = "editorial_narrative"
        case quotes
        case archetypeReactions = "archetype_reactions"
        case keyPoints = "key_points"
        case classification
        case summarizationDate = "summarization_date"
    }

    var narrativeParagraphs: [String] {
        editorialNarrative
            .split(separator: "\n\n")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }
}
