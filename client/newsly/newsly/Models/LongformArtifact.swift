//
//  LongformArtifact.swift
//  newsly
//
//  Typed long-form artifact models.
//

import Foundation

struct LongformFeedPreview: Codable, Equatable {
    let title: String
    let oneLine: String
    let previewBullets: [String]
    let reasonToRead: String
    let artifactType: String

    enum CodingKeys: String, CodingKey {
        case title
        case oneLine = "one_line"
        case previewBullets = "preview_bullets"
        case reasonToRead = "reason_to_read"
        case artifactType = "artifact_type"
    }
}

struct LongformSelectionTrace: Codable {
    let sourceHint: String
    let candidates: [String]
    let selected: String
    let reason: String
    let confidence: Double

    enum CodingKeys: String, CodingKey {
        case sourceHint = "source_hint"
        case candidates
        case selected
        case reason
        case confidence
    }
}

struct LongformSourceContext: Codable {
    let url: String
    let sourceName: String?
    let publicationDate: String?
    let platform: String?

    enum CodingKeys: String, CodingKey {
        case url
        case sourceName = "source_name"
        case publicationDate = "publication_date"
        case platform
    }
}

struct LongformArtifactQuote: Codable, Identifiable {
    let text: String
    let attribution: String?

    var id: String { text }
}

struct LongformArtifactKeyPoint: Codable, Identifiable {
    let heading: String
    let content: String

    var id: String { heading + String(content.prefix(20)) }
}

enum LongformArtifactDetailSectionKind: String {
    case takeaway
    case keyPoints
    case sourceQuotes
    case extra
}

enum LongformArtifactDetailSection: Identifiable {
    case takeaway(String)
    case keyPoints([LongformArtifactKeyPoint])
    case sourceQuotes([LongformArtifactQuote])
    case extra([LongformExtrasSection])

    var id: String { kind.rawValue }

    var kind: LongformArtifactDetailSectionKind {
        switch self {
        case .takeaway:
            return .takeaway
        case .keyPoints:
            return .keyPoints
        case .sourceQuotes:
            return .sourceQuotes
        case .extra:
            return .extra
        }
    }
}

struct LongformArtifactPayload: Codable {
    let overview: String?
    let quotes: [LongformArtifactQuote]
    let extrasRaw: [String: AnyCodable]
    let keyPoints: [LongformArtifactKeyPoint]
    let takeaway: String

    enum CodingKeys: String, CodingKey {
        case overview
        case quotes
        case extrasRaw = "extras"
        case keyPoints = "key_points"
        case takeaway
    }

}

struct LongformArtifactBody: Codable {
    let type: String
    let payload: LongformArtifactPayload
}

struct LongformArtifactEnvelope: Codable {
    let title: String
    let oneLine: String
    let ask: String
    let artifact: LongformArtifactBody
    let generatedAt: String?
    let sourceContext: LongformSourceContext?
    let selectionTrace: LongformSelectionTrace?
    let feedPreview: LongformFeedPreview?

    enum CodingKeys: String, CodingKey {
        case title
        case oneLine = "one_line"
        case ask
        case artifact
        case generatedAt = "generated_at"
        case sourceContext = "source_context"
        case selectionTrace = "selection_trace"
        case feedPreview = "feed_preview"
    }

    var detailSections: [LongformArtifactDetailSection] {
        let payload = artifact.payload
        var sections: [LongformArtifactDetailSection] = []

        let takeaway = payload.takeaway.trimmingCharacters(in: .whitespacesAndNewlines)
        if !takeaway.isEmpty {
            sections.append(.takeaway(takeaway))
        }

        if !payload.keyPoints.isEmpty {
            sections.append(.keyPoints(payload.keyPoints))
        }

        if !payload.quotes.isEmpty {
            sections.append(.sourceQuotes(payload.quotes))
        }

        let extras = LongformExtrasSection.orderedSections(from: payload.extrasRaw)
        if !extras.isEmpty {
            sections.append(.extra(extras))
        }

        return sections
    }
}

struct LongformExtrasSection: Identifiable {
    let title: String
    let items: [String]

    var id: String { title }

    private static let preferredGroups: [(title: String, keys: [String])] = [
        (title: "Evidence", keys: ["evidence"]),
        (
            title: "Mental Model",
            keys: ["mental_model", "what_it_explains", "when_to_use_it"]
        ),
        (
            title: "Counter Arguments",
            keys: ["counter_arguments", "counterpoint", "caveats", "limitations", "limits"]
        ),
        (
            title: "Supporting Arguments",
            keys: ["supporting_arguments", "arguments", "notable_arguments"]
        )
    ]

    static func orderedSections(from rawExtras: [String: AnyCodable]) -> [LongformExtrasSection] {
        let rawValues = rawExtras.mapValues(\.value)
        var usedKeys = Set<String>()
        var sections: [LongformExtrasSection] = []

        for group in preferredGroups {
            var groupedItems: [String] = []

            for key in group.keys {
                guard
                    let rawValue = rawValues[key],
                    let section = make(title: key, rawValue: rawValue)
                else {
                    continue
                }

                usedKeys.insert(key)
                let shouldPrefix = section.title != group.title
                groupedItems.append(
                    contentsOf: section.items.map { item in
                        shouldPrefix ? "\(section.title): \(item)" : item
                    }
                )
            }

            if !groupedItems.isEmpty {
                sections.append(LongformExtrasSection(title: group.title, items: groupedItems))
            }
        }

        let remainingSections = rawValues.keys
            .filter { !usedKeys.contains($0) }
            .compactMap { key -> LongformExtrasSection? in
                guard let rawValue = rawValues[key] else { return nil }
                return make(title: key, rawValue: rawValue)
            }
            .sorted { $0.title < $1.title }

        return sections + remainingSections
    }

    static func make(title rawTitle: String, rawValue: Any) -> LongformExtrasSection? {
        let title = rawTitle
            .replacingOccurrences(of: "_", with: " ")
            .capitalized

        if let string = rawValue as? String {
            let trimmed = string.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : LongformExtrasSection(title: title, items: [trimmed])
        }

        if let strings = rawValue as? [String] {
            let items = strings
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
            return items.isEmpty ? nil : LongformExtrasSection(title: title, items: items)
        }

        if let dictionaries = rawValue as? [[String: Any]] {
            let items = dictionaryItems(from: dictionaries)
            return items.isEmpty ? nil : LongformExtrasSection(title: title, items: items)
        }

        if let values = rawValue as? [Any] {
            let dictionaryItems = dictionaryItems(from: values)
            if !dictionaryItems.isEmpty {
                return LongformExtrasSection(title: title, items: dictionaryItems)
            }

            let stringItems = stringItems(from: values)
            return stringItems.isEmpty
                ? nil
                : LongformExtrasSection(title: title, items: stringItems)
        }

        return nil
    }

    private static func stringItems(from values: [Any]) -> [String] {
        values.compactMap { rawValue in
            guard let value = rawValue as? String else { return nil }
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
    }

    private static func dictionaryItems(from values: [Any]) -> [String] {
        dictionaryItems(from: values.compactMap { $0 as? [String: Any] })
    }

    private static func dictionaryItems(from dictionaries: [[String: Any]]) -> [String] {
        dictionaries.compactMap { dictionary in
            dictionary
                .sorted { $0.key < $1.key }
                .compactMap { key, value -> String? in
                    let text = "\(value)".trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !text.isEmpty, text != "<null>" else { return nil }
                    let label = key.replacingOccurrences(of: "_", with: " ").capitalized
                    return "\(label): \(text)"
                }
                .joined(separator: " · ")
        }
        .filter { !$0.isEmpty }
    }
}
