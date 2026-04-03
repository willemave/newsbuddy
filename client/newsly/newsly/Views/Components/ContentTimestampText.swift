//
//  ContentTimestampText.swift
//  newsly
//

import Foundation
import SwiftUI

enum ContentTimestampStyle {
    case detailMeta
    case compactRelative
}

enum ContentTimestampFormatter {
    private static let iso8601WithFractionalFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let iso8601Formatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let utcMicrosecondsFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
        return formatter
    }()

    private static let utcSecondsFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return formatter
    }()

    private static let monthDayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale.autoupdatingCurrent
        formatter.timeZone = TimeZone.autoupdatingCurrent
        formatter.dateFormat = "MMM d"
        return formatter
    }()

    private static let monthDayYearFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale.autoupdatingCurrent
        formatter.timeZone = TimeZone.autoupdatingCurrent
        formatter.dateFormat = "MMM d, yyyy"
        return formatter
    }()

    static func parse(_ rawValue: String?) -> Date? {
        guard let rawValue = rawValue?.trimmingCharacters(in: .whitespacesAndNewlines),
              !rawValue.isEmpty else {
            return nil
        }

        if let date = iso8601WithFractionalFormatter.date(from: rawValue) {
            return date
        }

        if let date = iso8601Formatter.date(from: rawValue) {
            return date
        }

        if let date = utcMicrosecondsFormatter.date(from: rawValue) {
            return date
        }

        return utcSecondsFormatter.date(from: rawValue)
    }

    static func detailMetaText(from rawValue: String?, now: Date = Date()) -> String? {
        guard let date = parse(rawValue) else { return nil }

        let interval = now.timeIntervalSince(date)
        if interval >= 0, interval < 7 * 24 * 60 * 60 {
            let formatter = RelativeDateTimeFormatter()
            formatter.unitsStyle = .short
            return formatter.localizedString(for: date, relativeTo: now)
        }

        let calendar = Calendar.current
        if calendar.isDate(date, equalTo: now, toGranularity: .year) {
            return monthDayFormatter.string(from: date)
        }

        return monthDayYearFormatter.string(from: date)
    }

    static func compactRelativeText(from rawValue: String?, now: Date = Date()) -> String? {
        guard let date = parse(rawValue) else { return nil }

        let interval = now.timeIntervalSince(date)

        if interval >= 0, interval < 60 {
            return "now"
        }

        if interval >= 60, interval < 3600 {
            return "\(Int(interval / 60))m ago"
        }

        if interval >= 3600, interval < 86_400 {
            return "\(Int(interval / 3600))h ago"
        }

        if interval >= 86_400, interval < 604_800 {
            return "\(Int(interval / 86_400))d ago"
        }

        return monthDayFormatter.string(from: date)
    }

    static func text(from rawValue: String?, style: ContentTimestampStyle, now: Date = Date()) -> String? {
        switch style {
        case .detailMeta:
            return detailMetaText(from: rawValue, now: now)
        case .compactRelative:
            return compactRelativeText(from: rawValue, now: now)
        }
    }
}

struct ContentTimestampText: View {
    let rawValue: String?
    let style: ContentTimestampStyle
    var fallback: String? = nil

    private var resolvedText: String? {
        ContentTimestampFormatter.text(from: rawValue, style: style) ?? fallback
    }

    var body: some View {
        if let resolvedText {
            Text(resolvedText)
        }
    }
}
