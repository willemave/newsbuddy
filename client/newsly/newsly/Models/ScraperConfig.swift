//
//  ScraperConfig.swift
//  newsly
//

import Foundation

struct ScraperConfigStats: Codable {
    let totalCount: Int
    let completedCount: Int
    let unreadCount: Int
    let processingCount: Int
    let latestProcessedAt: String?
    let latestPublicationAt: String?
    let nextExpectedAt: String?
    let averageIntervalHours: Double?
    let intervalSampleSize: Int

    enum CodingKeys: String, CodingKey {
        case totalCount = "total_count"
        case completedCount = "completed_count"
        case unreadCount = "unread_count"
        case processingCount = "processing_count"
        case latestProcessedAt = "latest_processed_at"
        case latestPublicationAt = "latest_publication_at"
        case nextExpectedAt = "next_expected_at"
        case averageIntervalHours = "average_interval_hours"
        case intervalSampleSize = "interval_sample_size"
    }

    var latestProcessedDate: Date? {
        Self.parseISODate(latestProcessedAt)
    }

    var latestPublicationDate: Date? {
        Self.parseISODate(latestPublicationAt)
    }

    var nextExpectedDate: Date? {
        Self.parseISODate(nextExpectedAt)
    }

    var compactCountSummary: String? {
        var parts: [String] = []
        if unreadCount > 0 {
            parts.append("\(unreadCount) unread")
        }
        if processingCount > 0 {
            parts.append("\(processingCount) processing")
        }
        if totalCount > 0 {
            parts.append("\(totalCount) items")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " • ")
    }

    var relativeProcessedSummary: String? {
        guard let latestProcessedDate else { return nil }
        return "Last processed \(Self.relativeFormatter.localizedString(for: latestProcessedDate, relativeTo: Date()))"
    }

    var nextExpectedSummary: String? {
        guard let nextExpectedDate else { return nil }

        let now = Date()
        if abs(nextExpectedDate.timeIntervalSince(now)) < 3600 {
            return "Expected around now"
        }
        if nextExpectedDate > now {
            let relative = Self.relativeFormatter.localizedString(for: nextExpectedDate, relativeTo: now)
            return "Likely \(relative)"
        }

        let relative = Self.relativeFormatter.localizedString(for: nextExpectedDate, relativeTo: now)
        return "Overdue \(relative)"
    }

    var cadenceSummary: String? {
        guard let averageIntervalHours else { return nil }
        if averageIntervalHours < 24 {
            return String(format: "Usually every %.0f hr", averageIntervalHours)
        }
        let days = averageIntervalHours / 24
        return String(format: "Usually every %.1f d", days)
    }

    var hasVisibleStats: Bool {
        totalCount > 0 || processingCount > 0 || latestProcessedAt != nil || nextExpectedAt != nil
    }

    private static let relativeFormatter: RelativeDateTimeFormatter = {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter
    }()

    private static func parseISODate(_ value: String?) -> Date? {
        ServerDate.parse(value)
    }
}

struct ScraperConfig: Identifiable, Codable {
    let id: Int
    let scraperType: String
    let displayName: String?
    let config: [String: AnyCodable]
    let limit: Int?
    let isActive: Bool
    let createdAt: String
    let stats: ScraperConfigStats?

    var feedURL: String? {
        if let feedValue = config["feed_url"]?.value as? String {
            return feedValue
        }
        if let urlValue = config["url"]?.value as? String {
            return urlValue
        }
        return nil
    }

    enum CodingKeys: String, CodingKey {
        case id
        case scraperType = "scraper_type"
        case displayName = "display_name"
        case config
        case limit
        case isActive = "is_active"
        case createdAt = "created_at"
        case stats
    }
}
