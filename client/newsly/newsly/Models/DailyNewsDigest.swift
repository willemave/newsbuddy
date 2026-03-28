//
//  DailyNewsDigest.swift
//  newsly
//

import Foundation

struct DailyNewsDigestCitation: Codable, Identifiable {
    let contentId: Int
    let label: String?
    let title: String
    let url: String?

    enum CodingKeys: String, CodingKey {
        case contentId = "content_id"
        case label
        case title
        case url
    }

    var id: String {
        "\(contentId):\(url ?? title)"
    }
}

struct DailyNewsDigestBulletDetail: Codable, Identifiable {
    let text: String
    let sourceCount: Int
    let citations: [DailyNewsDigestCitation]
    let commentQuotes: [String]

    enum CodingKeys: String, CodingKey {
        case text
        case sourceCount = "source_count"
        case citations
        case commentQuotes = "comment_quotes"
    }

    var id: String {
        text
    }

    var cleanedText: String {
        text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var digestPreviewText: String {
        var preview = cleanedText

        for quote in cleanedCommentQuotes where !quote.isEmpty {
            let suffixes = [
                " \"\(quote)\"",
                " “\(quote)”",
                " '\(quote)'",
                " ‘\(quote)’",
                " \(quote)"
            ]

            for suffix in suffixes where preview.hasSuffix(suffix) {
                preview.removeLast(suffix.count)
                preview = preview.trimmingCharacters(in: .whitespacesAndNewlines)
                break
            }
        }

        return preview
    }

    var cleanedCommentQuotes: [String] {
        commentQuotes
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }
}

struct DailyNewsDigest: Codable, Identifiable {
    let id: Int
    let localDate: String
    let timezone: String
    let title: String
    let summary: String
    let keyPoints: [String]
    let bulletDetails: [DailyNewsDigestBulletDetail]
    let sourceCount: Int
    let sourceContentIds: [Int]
    let sourceLabels: [String]
    var isRead: Bool
    var readAt: String?
    let generatedAt: String
    let coverageEndAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case localDate = "local_date"
        case timezone
        case title
        case summary
        case keyPoints = "key_points"
        case bulletDetails = "bullet_details"
        case sourceCount = "source_count"
        case sourceContentIds = "source_content_ids"
        case sourceLabels = "source_labels"
        case isRead = "is_read"
        case readAt = "read_at"
        case generatedAt = "generated_at"
        case coverageEndAt = "coverage_end_at"
    }

    private static let localDateParser: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    private static let dayLabelFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "EEEE, MMM d"
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    private static let timeLabelFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    var localDateValue: Date? {
        Self.localDateParser.date(from: localDate)
    }

    var displayDateLabel: String {
        guard let date = localDateValue else { return localDate }
        let calendar = Calendar.current
        if calendar.isDateInToday(date) {
            return "Today"
        }
        if calendar.isDateInYesterday(date) {
            return "Yesterday"
        }
        return Self.dayLabelFormatter.string(from: date)
    }

    var displayTimeLabel: String {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var date = parser.date(from: generatedAt)
        if date == nil {
            parser.formatOptions = [.withInternetDateTime]
            date = parser.date(from: generatedAt)
        }
        guard let date else { return "" }
        return Self.timeLabelFormatter.string(from: date)
    }

    var cleanedSummary: String {
        summary.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var cleanedKeyPoints: [String] {
        keyPoints.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
    }

    var displayBulletDetails: [DailyNewsDigestBulletDetail] {
        if !bulletDetails.isEmpty {
            return bulletDetails.filter { !$0.cleanedText.isEmpty }
        }
        return cleanedKeyPoints.map {
            DailyNewsDigestBulletDetail(
                text: $0,
                sourceCount: 0,
                citations: [],
                commentQuotes: []
            )
        }
    }

    var cleanedSourceLabels: [String] {
        sourceLabels.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
    }

    var showsDigDeeperAction: Bool {
        !displayBulletDetails.isEmpty
    }
}

struct DailyNewsDigestListResponse: Codable {
    let digests: [DailyNewsDigest]
    let meta: PaginationMetadata

    var nextCursor: String? { meta.nextCursor }
    var hasMore: Bool { meta.hasMore }
}
