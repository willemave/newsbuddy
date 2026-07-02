//
//  ScraperConfig.swift
//  newsly
//

import Foundation

typealias ScraperConfigStats = APIScraperConfigStatsResponse
typealias ScraperConfig = APIScraperConfigResponse

extension APIScraperConfigStatsResponse {
    var latestProcessedDate: Date? {
        latestProcessedAt
    }

    var latestPublicationDate: Date? {
        latestPublicationAt
    }

    var nextExpectedDate: Date? {
        nextExpectedAt
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
}

extension APIScraperConfigResponse: Identifiable {}

extension APIScraperConfigResponse {
    var feedURL: String? {
        if let feedUrl {
            return feedUrl
        }
        if let feedValue = config["feed_url"]?.value as? String {
            return feedValue
        }
        if let urlValue = config["url"]?.value as? String {
            return urlValue
        }
        return nil
    }
}
