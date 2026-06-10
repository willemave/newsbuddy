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

// Custom decoding keeps optional-array fields tolerant: a single missing backend
// key falls back to an empty array instead of voiding the whole summary.
extension StructuredSummary {
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        overview = try container.decodeIfPresent(String.self, forKey: .overview)
        bulletPoints = try container.decodeIfPresent([BulletPoint].self, forKey: .bulletPoints) ?? []
        quotes = try container.decodeIfPresent([Quote].self, forKey: .quotes) ?? []
        topics = try container.decodeIfPresent([String].self, forKey: .topics) ?? []
        questions = try container.decodeIfPresent([String].self, forKey: .questions)
        counterArguments = try container.decodeIfPresent([String].self, forKey: .counterArguments)
        summarizationDate = try container.decodeIfPresent(String.self, forKey: .summarizationDate)
        classification = try container.decodeIfPresent(String.self, forKey: .classification)
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

// Tolerant decoding: missing array keys fall back to empty arrays.
extension InterleavedSummaryV2 {
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        hook = try container.decode(String.self, forKey: .hook)
        keyPoints = try container.decodeIfPresent([BulletPoint].self, forKey: .keyPoints) ?? []
        topics = try container.decodeIfPresent([InterleavedTopic].self, forKey: .topics) ?? []
        quotes = try container.decodeIfPresent([Quote].self, forKey: .quotes) ?? []
        takeaway = try container.decode(String.self, forKey: .takeaway)
        classification = try container.decodeIfPresent(String.self, forKey: .classification)
        summarizationDate = try container.decodeIfPresent(String.self, forKey: .summarizationDate)
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

// Tolerant decoding: a missing points key falls back to an empty array.
extension BulletedSummary {
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        points = try container.decodeIfPresent([BulletSummaryPoint].self, forKey: .points) ?? []
        classification = try container.decodeIfPresent(String.self, forKey: .classification)
        summarizationDate = try container.decodeIfPresent(String.self, forKey: .summarizationDate)
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

struct EditorialDetailSection: Identifiable {
    let title: String
    let items: [String]

    var id: String { title }
}

struct EditorialNarrativeSummary: Codable {
    let title: String?
    let editorialNarrative: String
    let quotes: [Quote]
    let archetypeReactions: [EditorialArchetypeReaction]?
    let keyPoints: [EditorialKeyPoint]
    let sourceDetailsRaw: [String: AnyCodable]?
    let classification: String?
    let summarizationDate: String?

    enum CodingKeys: String, CodingKey {
        case title
        case editorialNarrative = "editorial_narrative"
        case quotes
        case archetypeReactions = "archetype_reactions"
        case keyPoints = "key_points"
        case sourceDetailsRaw = "source_details"
        case classification
        case summarizationDate = "summarization_date"
    }

    var narrativeParagraphs: [String] {
        editorialNarrative
            .split(separator: "\n\n")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    var sourceTemplate: String? {
        sourceDetailsRaw?["template"]?.value as? String
    }

    var sourceTemplateDisplayName: String? {
        switch sourceTemplate {
        case "podcast":
            return "Podcast Frame"
        case "substack":
            return "Essay Frame"
        case "twitter":
            return "Post Frame"
        case "research":
            return "Research Frame"
        case "github":
            return "Repo Frame"
        default:
            return nil
        }
    }

    var sourceDetailSections: [EditorialDetailSection] {
        switch sourceTemplate {
        case "podcast":
            return compactSections(
                ("Thesis", [stringValue("thesis")].compactMap { $0 }),
                ("Speakers", stringArray("speakers")),
                ("Notable Arguments", stringArray("notable_arguments")),
                ("Practical Takeaways", stringArray("practical_takeaways"))
            )
        case "substack":
            return compactSections(
                ("Thesis", [stringValue("thesis")].compactMap { $0 }),
                ("Supporting Arguments", stringArray("supporting_arguments")),
                ("Evidence", stringArray("evidence")),
                ("Implications", stringArray("implications"))
            )
        case "twitter":
            return compactSections(
                ("Primary Claim", [stringValue("primary_claim")].compactMap { $0 }),
                ("Evidence", stringArray("evidence")),
                ("Caveats", stringArray("caveats")),
                ("Linked Context", stringArray("linked_context"))
            )
        case "research":
            return compactSections(
                ("Hypothesis", [stringValue("hypothesis")].compactMap { $0 }),
                ("Methods", stringArray("methods")),
                ("Arguments", stringArray("arguments")),
                ("Limitations", stringArray("limitations")),
                ("Implications", stringArray("implications"))
            )
        case "github":
            return compactSections(
                ("Overview", [stringValue("overview")].compactMap { $0 }),
                ("Architecture", stringArray("architecture")),
                ("Interfaces", stringArray("interfaces")),
                ("Setup Constraints", stringArray("setup_constraints")),
                ("Maturity Signals", stringArray("maturity_signals")),
                ("Best-Fit Use Cases", stringArray("best_fit_use_cases"))
            )
        default:
            return []
        }
    }

    private func stringValue(_ key: String) -> String? {
        guard let value = sourceDetailsRaw?[key]?.value as? String else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func stringArray(_ key: String) -> [String] {
        guard let values = sourceDetailsRaw?[key]?.value as? [Any] else { return [] }
        return values.compactMap { rawValue in
            guard let value = rawValue as? String else { return nil }
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
    }

    private func compactSections(
        _ candidates: (String, [String])...
    ) -> [EditorialDetailSection] {
        candidates.compactMap { title, items in
            guard !items.isEmpty else { return nil }
            return EditorialDetailSection(title: title, items: items)
        }
    }
}

// Tolerant decoding: missing array keys fall back to empty arrays.
extension EditorialNarrativeSummary {
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        editorialNarrative = try container.decode(String.self, forKey: .editorialNarrative)
        quotes = try container.decodeIfPresent([Quote].self, forKey: .quotes) ?? []
        archetypeReactions = try container.decodeIfPresent(
            [EditorialArchetypeReaction].self,
            forKey: .archetypeReactions
        )
        keyPoints = try container.decodeIfPresent([EditorialKeyPoint].self, forKey: .keyPoints) ?? []
        sourceDetailsRaw = try container.decodeIfPresent(
            [String: AnyCodable].self,
            forKey: .sourceDetailsRaw
        )
        classification = try container.decodeIfPresent(String.self, forKey: .classification)
        summarizationDate = try container.decodeIfPresent(String.self, forKey: .summarizationDate)
    }
}
