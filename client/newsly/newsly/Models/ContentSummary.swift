//
//  ContentSummary.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation

enum SavedLibraryItemState: Equatable {
    case processing
    case ready
    case unavailable
}

struct ContentSummary: Identifiable, Equatable {
    let id: Int
    let contentType: APIContentType
    let url: String
    let title: String?
    let source: String?
    let platform: String?
    let status: APIContentStatus
    let shortSummary: String?
    let createdAt: String
    let processedAt: String?
    let isRead: Bool
    var isSavedToKnowledge: Bool
    let knowledgeSavedAt: String?
    let imageUrl: String?
    let thumbnailUrl: String?
    let savedSource: String?
    private let cachedProcessedDate: Date?
    private let cachedKnowledgeSavedDate: Date?

    private static let processedDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .none
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    private static func parseDate(_ dateString: String) -> Date? {
        ContentTimestampFormatter.parse(dateString)
    }

    init(
        id: Int,
        contentType: APIContentType,
        url: String,
        title: String?,
        source: String?,
        platform: String?,
        status: APIContentStatus,
        shortSummary: String?,
        createdAt: String,
        processedAt: String?,
        isRead: Bool,
        isSavedToKnowledge: Bool,
        knowledgeSavedAt: String? = nil,
        imageUrl: String? = nil,
        thumbnailUrl: String? = nil,
        savedSource: String? = nil
    ) {
        self.id = id
        self.contentType = contentType
        self.url = url
        self.title = title
        self.source = source
        self.platform = platform
        self.status = status
        self.shortSummary = shortSummary
        self.createdAt = createdAt
        self.processedAt = processedAt
        self.isRead = isRead
        self.isSavedToKnowledge = isSavedToKnowledge
        self.knowledgeSavedAt = knowledgeSavedAt
        self.imageUrl = imageUrl
        self.thumbnailUrl = thumbnailUrl
        self.savedSource = savedSource
        let processedDate = processedAt.flatMap(Self.parseDate)
        let knowledgeSavedDate = knowledgeSavedAt.flatMap(Self.parseDate)
        self.cachedProcessedDate = processedDate
        self.cachedKnowledgeSavedDate = knowledgeSavedDate
    }

    init(api response: APIContentSummaryResponse) {
        self.init(
            id: response.id,
            contentType: response.contentType,
            url: response.url,
            title: response.title,
            source: response.source,
            platform: response.platform,
            status: response.status,
            shortSummary: response.shortSummary,
            createdAt: ServerDate.format(response.createdAt),
            processedAt: response.processedAt.map(ServerDate.format),
            isRead: response.isRead,
            isSavedToKnowledge: response.isSavedToKnowledge,
            knowledgeSavedAt: response.knowledgeSavedAt.map(ServerDate.format),
            imageUrl: response.imageUrl,
            thumbnailUrl: response.thumbnailUrl,
            savedSource: response.savedSource?.rawValue
        )
    }

    init(api response: APINewsItemSummaryResponse) {
        self.init(
            id: response.id,
            contentType: response.contentType,
            url: response.url,
            title: response.title,
            source: response.source,
            platform: response.platform,
            status: response.status,
            shortSummary: response.shortSummary,
            createdAt: ServerDate.format(response.createdAt),
            processedAt: response.processedAt.map(ServerDate.format),
            isRead: response.isRead,
            isSavedToKnowledge: response.isSavedToKnowledge
        )
    }

    var displayTitle: String {
        Self.normalizedText(title) ?? URL(string: url)?.host ?? "Untitled"
    }

    var savedLibraryItemState: SavedLibraryItemState {
        switch status {
        case .new, .pending, .processing, .awaiting_image:
            return .processing
        case .completed:
            return .ready
        case .failed, .skipped, .unknown:
            return .unavailable
        }
    }

    var isXBookmark: Bool {
        savedSource == APISavedSource.x_bookmark.rawValue
    }

    var knowledgeSourceLabels: [String] {
        let source = Self.normalizedText(source)
            .flatMap { value in
                value.caseInsensitiveCompare("self submission") == .orderedSame ? nil : value
            }

        if isXBookmark {
            return ["X Bookmark"] + (source.map { [$0] } ?? [])
        }

        if let source {
            return [source]
        }

        if let host = URL(string: url)?.host {
            return [host.replacingOccurrences(
                of: "^www\\.",
                with: "",
                options: [.regularExpression, .caseInsensitive]
            )]
        }

        if let platform = Self.normalizedText(platform),
           platform.caseInsensitiveCompare("self submission") != .orderedSame {
            return [platform]
        }

        return ["Saved"]
    }

    private static func normalizedText(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    var summaryDisplayText: String? {
        guard contentType != .news else { return nil }
        return Self.normalizedText(shortSummary)
    }

    var processedDateDisplay: String? {
        guard let date = cachedProcessedDate else {
            return nil
        }

        return Self.processedDateFormatter.string(from: date)
    }

    var knowledgeActivityDate: Date {
        cachedKnowledgeSavedDate ?? Self.parseDate(createdAt) ?? .distantPast
    }

    var knowledgeRelativeTimeDisplay: String? {
        guard knowledgeActivityDate != .distantPast else { return nil }
        return ContentTimestampFormatter.compactRelativeText(from: knowledgeActivityDate)
    }

    func updating(
        isRead: Bool? = nil,
        isSavedToKnowledge: Bool? = nil
    ) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: contentType,
            url: url,
            title: title,
            source: source,
            platform: platform,
            status: status,
            shortSummary: shortSummary,
            createdAt: createdAt,
            processedAt: processedAt,
            isRead: isRead ?? self.isRead,
            isSavedToKnowledge: isSavedToKnowledge ?? self.isSavedToKnowledge,
            knowledgeSavedAt: knowledgeSavedAt,
            imageUrl: imageUrl,
            thumbnailUrl: thumbnailUrl,
            savedSource: savedSource
        )
    }
}
