//
//  SourceMetadata.swift
//  newsly
//

import Foundation

struct SourceMetadataAuthor: Codable, Identifiable, Hashable {
    let name: String
    let affiliation: String?
    let affiliationSource: String?
    let confidence: String?

    enum CodingKeys: String, CodingKey {
        case name
        case affiliation
        case affiliationSource = "affiliation_source"
        case confidence
    }

    var id: String {
        [name, affiliation].compactMap { $0 }.joined(separator: "|")
    }

    var displayName: String? {
        name.nonEmptyTrimmed
    }

    var displayAffiliation: String? {
        affiliation?.nonEmptyTrimmed
    }
}

struct SourceMetadataCategory: Codable, Identifiable, Hashable {
    let term: String
    let primary: Bool?

    var id: String { term }

    var displayTerm: String? {
        term.nonEmptyTrimmed
    }
}

struct SourceMetadata: Codable, Hashable {
    let schemaVersion: Int?
    let kind: String?
    let provider: String?
    let sourceID: String?
    let canonicalAbsURL: String?
    let pdfURL: String?
    let title: String?
    let abstractText: String?
    let briefSynopsis: String?
    let authors: [SourceMetadataAuthor]
    let categories: [SourceMetadataCategory]
    let publishedAt: String?
    let updatedAt: String?
    let doi: String?
    let journalRef: String?
    let comment: String?
    let extractedAt: String?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case kind
        case provider
        case sourceID = "source_id"
        case canonicalAbsURL = "canonical_abs_url"
        case pdfURL = "pdf_url"
        case title
        case abstractText = "abstract"
        case briefSynopsis = "brief_synopsis"
        case authors
        case categories
        case publishedAt = "published_at"
        case updatedAt = "updated_at"
        case doi
        case journalRef = "journal_ref"
        case comment
        case extractedAt = "extracted_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion)
        kind = try container.decodeIfPresent(String.self, forKey: .kind)
        provider = try container.decodeIfPresent(String.self, forKey: .provider)
        sourceID = try container.decodeIfPresent(String.self, forKey: .sourceID)
        canonicalAbsURL = try container.decodeIfPresent(String.self, forKey: .canonicalAbsURL)
        pdfURL = try container.decodeIfPresent(String.self, forKey: .pdfURL)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        abstractText = try container.decodeIfPresent(String.self, forKey: .abstractText)
        briefSynopsis = try container.decodeIfPresent(String.self, forKey: .briefSynopsis)
        authors = try container.decodeIfPresent([SourceMetadataAuthor].self, forKey: .authors) ?? []
        categories = try container.decodeIfPresent([SourceMetadataCategory].self, forKey: .categories) ?? []
        publishedAt = try container.decodeIfPresent(String.self, forKey: .publishedAt)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
        doi = try container.decodeIfPresent(String.self, forKey: .doi)
        journalRef = try container.decodeIfPresent(String.self, forKey: .journalRef)
        comment = try container.decodeIfPresent(String.self, forKey: .comment)
        extractedAt = try container.decodeIfPresent(String.self, forKey: .extractedAt)
    }

    var displaySynopsis: String? {
        normalized(briefSynopsis) ?? normalized(abstractText)
    }

    var displayAuthors: [SourceMetadataAuthor] {
        authors.filter { $0.displayName != nil }
    }

    var categoryLine: String? {
        let terms = categories.compactMap(\.displayTerm)
        guard !terms.isEmpty else { return nil }
        return terms.joined(separator: ", ")
    }

    var publishedDateDisplay: String? {
        guard let publishedAt = normalized(publishedAt) else { return nil }
        guard let date = ServerDate.parse(publishedAt) else {
            return String(publishedAt.prefix(10))
        }
        return Self.displayDateFormatter.string(from: date)
    }

    var arxivURL: String? {
        normalized(canonicalAbsURL) ?? normalized(pdfURL)
    }

    var isDisplayable: Bool {
        displaySynopsis != nil || !displayAuthors.isEmpty || categoryLine != nil || arxivURL != nil
    }

    private func normalized(_ value: String?) -> String? {
        value?.nonEmptyTrimmed
    }

    private static let displayDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .none
        return formatter
    }()
}

private extension String {
    var nonEmptyTrimmed: String? {
        let trimmed = trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
