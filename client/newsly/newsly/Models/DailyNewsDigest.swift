//
//  DailyNewsDigest.swift
//  newsly
//

import Foundation

struct DailyNewsDigest: Codable, Identifiable {
    let id: Int
    let localDate: String
    let timezone: String
    let title: String
    let summary: String
    let keyPoints: [String]
    let sourceCount: Int
    let sourceContentIds: [Int]
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
        case sourceCount = "source_count"
        case sourceContentIds = "source_content_ids"
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

    private static let coverageParser = ISO8601DateFormatter()

    private static let coverageLabelFormatter: DateFormatter = {
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

    var displayCoverageLabel: String? {
        guard let date = localDateValue, Calendar.current.isDateInToday(date) else {
            return nil
        }
        guard let coverageEndAt, let parsed = Self.coverageParser.date(from: coverageEndAt) else {
            return nil
        }
        return "Updated through \(Self.coverageLabelFormatter.string(from: parsed))"
    }

    var cleanedSummary: String {
        summary.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var cleanedKeyPoints: [String] {
        keyPoints.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
    }

    var showsDigDeeperAction: Bool {
        sourceCount > 0 && (!cleanedKeyPoints.isEmpty || !cleanedSummary.isEmpty)
    }
}

struct DailyNewsDigestListResponse: Codable {
    let digests: [DailyNewsDigest]
    let meta: PaginationMetadata

    var nextCursor: String? { meta.nextCursor }
    var hasMore: Bool { meta.hasMore }
}

struct DailyNewsDigestVoiceSummaryResponse: Codable {
    let digestId: Int
    let title: String
    let narrationText: String

    enum CodingKeys: String, CodingKey {
        case digestId = "digest_id"
        case title
        case narrationText = "narration_text"
    }
}
