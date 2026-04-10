//
//  SubmissionStatusItem.swift
//  newsly
//
//  Created by Assistant on 1/14/26.
//

import Foundation

struct SubmissionStatusItem: Codable, Identifiable {
    let id: Int
    let contentType: String
    let url: String
    let sourceUrl: String?
    let title: String?
    let status: String
    let errorMessage: String?
    let createdAt: String
    let processedAt: String?
    let submittedVia: String?
    let isSelfSubmission: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case contentType = "content_type"
        case url
        case sourceUrl = "source_url"
        case title
        case status
        case errorMessage = "error_message"
        case createdAt = "created_at"
        case processedAt = "processed_at"
        case submittedVia = "submitted_via"
        case isSelfSubmission = "is_self_submission"
    }

    var displayTitle: String {
        if let title, !title.isEmpty {
            return title
        }
        if let host = URL(string: url)?.host {
            return host
        }
        return url
    }

    var statusLabel: String {
        switch status.lowercased() {
        case "new", "pending":
            return "Queued"
        case "processing":
            return "Processing"
        case "failed":
            return "Failed"
        case "skipped":
            return "Skipped"
        default:
            return status.capitalized
        }
    }

    var isError: Bool {
        let normalized = status.lowercased()
        return normalized == "failed" || normalized == "skipped"
    }

    var errorDisplayText: String? {
        guard isError else { return nil }
        if let errorMessage, !errorMessage.isEmpty {
            return errorMessage
        }
        return status.lowercased() == "skipped" ? "Processing was skipped." : "Processing failed."
    }

    var statusDateDisplay: String? {
        let dateString = processedAt ?? createdAt
        guard let date = parseDate(from: dateString) else { return nil }

        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        formatter.timeZone = TimeZone.current
        return formatter.string(from: date)
    }

    var createdDate: Date? {
        parseDate(from: createdAt)
    }

    private func parseDate(from dateString: String) -> Date? {
        let iso8601WithFractional = ISO8601DateFormatter()
        iso8601WithFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso8601WithFractional.date(from: dateString) {
            return date
        }

        let iso8601 = ISO8601DateFormatter()
        iso8601.formatOptions = [.withInternetDateTime]
        if let date = iso8601.date(from: dateString) {
            return date
        }

        let formatterWithMicroseconds = DateFormatter()
        formatterWithMicroseconds.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
        formatterWithMicroseconds.timeZone = TimeZone(abbreviation: "UTC")
        if let date = formatterWithMicroseconds.date(from: dateString) {
            return date
        }

        let formatterWithoutMicroseconds = DateFormatter()
        formatterWithoutMicroseconds.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        formatterWithoutMicroseconds.timeZone = TimeZone(abbreviation: "UTC")
        if let date = formatterWithoutMicroseconds.date(from: dateString) {
            return date
        }

        return nil
    }
}

struct SubmissionStatusListResponse: Codable {
    let submissions: [SubmissionStatusItem]
    let meta: PaginationMetadata

    enum CodingKeys: String, CodingKey {
        case submissions
        case meta
    }

    var nextCursor: String? { meta.nextCursor }
    var hasMore: Bool { meta.hasMore }
    var pageSize: Int { meta.pageSize }
}
