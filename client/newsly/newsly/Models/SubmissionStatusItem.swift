//
//  SubmissionStatusItem.swift
//  newsly
//
//  Created by Assistant on 1/14/26.
//

import Foundation

struct SubmissionFeedInitialDownload: Codable {
    let requestedCount: Int?
    let ran: Bool?
    let status: String?
    let reason: String?
    let error: String?
    let configId: Int?
    let baseLimit: Int?
    let targetLimit: Int?
    let scraped: Int?
    let saved: Int?
    let duplicates: Int?
    let errors: Int?

    enum CodingKeys: String, CodingKey {
        case requestedCount = "requested_count"
        case ran
        case status
        case reason
        case error
        case configId = "config_id"
        case baseLimit = "base_limit"
        case targetLimit = "target_limit"
        case scraped
        case saved
        case duplicates
        case errors
    }

    init(
        requestedCount: Int? = nil,
        ran: Bool? = nil,
        status: String? = nil,
        reason: String? = nil,
        error: String? = nil,
        configId: Int? = nil,
        baseLimit: Int? = nil,
        targetLimit: Int? = nil,
        scraped: Int? = nil,
        saved: Int? = nil,
        duplicates: Int? = nil,
        errors: Int? = nil
    ) {
        self.requestedCount = requestedCount
        self.ran = ran
        self.status = status
        self.reason = reason
        self.error = error
        self.configId = configId
        self.baseLimit = baseLimit
        self.targetLimit = targetLimit
        self.scraped = scraped
        self.saved = saved
        self.duplicates = duplicates
        self.errors = errors
    }
}

struct SubmissionFeedSubscription: Codable {
    let status: String
    let feedUrl: String?
    let feedType: String?
    let created: Bool?
    let configId: Int?
    let initialDownload: SubmissionFeedInitialDownload?

    enum CodingKeys: String, CodingKey {
        case status
        case feedUrl = "feed_url"
        case feedType = "feed_type"
        case created
        case configId = "config_id"
        case initialDownload = "initial_download"
    }

    init(
        status: String,
        feedUrl: String? = nil,
        feedType: String? = nil,
        created: Bool? = nil,
        configId: Int? = nil,
        initialDownload: SubmissionFeedInitialDownload? = nil
    ) {
        self.status = status
        self.feedUrl = feedUrl
        self.feedType = feedType
        self.created = created
        self.configId = configId
        self.initialDownload = initialDownload
    }
}

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
    let submissionKind: String?
    let outcome: String?
    let detectedFeed: DetectedFeed?
    let feedSubscription: SubmissionFeedSubscription?

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
        case submissionKind = "submission_kind"
        case outcome
        case detectedFeed = "detected_feed"
        case feedSubscription = "feed_subscription"
    }

    init(
        id: Int,
        contentType: String,
        url: String,
        sourceUrl: String?,
        title: String?,
        status: String,
        errorMessage: String?,
        createdAt: String,
        processedAt: String?,
        submittedVia: String?,
        isSelfSubmission: Bool,
        submissionKind: String? = nil,
        outcome: String? = nil,
        detectedFeed: DetectedFeed? = nil,
        feedSubscription: SubmissionFeedSubscription? = nil
    ) {
        self.id = id
        self.contentType = contentType
        self.url = url
        self.sourceUrl = sourceUrl
        self.title = title
        self.status = status
        self.errorMessage = errorMessage
        self.createdAt = createdAt
        self.processedAt = processedAt
        self.submittedVia = submittedVia
        self.isSelfSubmission = isSelfSubmission
        self.submissionKind = submissionKind
        self.outcome = outcome
        self.detectedFeed = detectedFeed
        self.feedSubscription = feedSubscription
    }

    var displayTitle: String {
        if isFeedSubscription {
            if let feedTitle = detectedFeed?.title, !feedTitle.isEmpty {
                return feedTitle
            }
            if let feedUrl = feedSubscription?.feedUrl ?? detectedFeed?.url,
               let host = URL(string: feedUrl)?.host {
                return host
            }
        }
        if let title, !title.isEmpty {
            return title
        }
        if let host = URL(string: url)?.host {
            return host
        }
        return url
    }

    var statusLabel: String {
        switch effectiveOutcome {
        case "new", "pending":
            return "Queued"
        case "queued":
            return "Queued"
        case "processing":
            return "Processing"
        case "completed":
            return "Completed"
        case "failed":
            return "Failed"
        case "skipped":
            return "Skipped"
        case "subscribed":
            return "Subscribed"
        case "already_subscribed":
            return "Already subscribed"
        case "feed_not_found":
            return "Feed not found"
        case "feed_fetch_failed":
            return "Couldn't check feed"
        case "feed_subscription_failed":
            return "Couldn't add feed"
        default:
            return effectiveOutcome.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    var isError: Bool {
        switch effectiveOutcome {
        case "failed", "skipped", "feed_not_found", "feed_fetch_failed", "feed_subscription_failed":
            return true
        default:
            return false
        }
    }

    var errorDisplayText: String? {
        guard isError else { return nil }
        if let errorMessage, !errorMessage.isEmpty {
            return errorMessage
        }
        switch effectiveOutcome {
        case "skipped":
            return "Processing was skipped."
        case "feed_not_found":
            return "No RSS or Atom feed was found for this URL."
        case "feed_fetch_failed":
            return "The page could not be checked for feeds."
        case "feed_subscription_failed":
            return "The feed could not be added."
        default:
            return "Processing failed."
        }
    }

    var statusDetailText: String? {
        if let errorDisplayText {
            return errorDisplayText
        }
        guard isFeedSubscription else { return nil }
        switch effectiveOutcome {
        case "subscribed":
            if feedSubscription?.initialDownload?.status?.lowercased() == "failed" {
                return "Feed added, but recent items could not be downloaded."
            }
            if let saved = feedSubscription?.initialDownload?.saved, saved > 0 {
                return "Feed added; \(saved) recent item\(saved == 1 ? "" : "s") saved."
            }
            return "Feed added."
        case "already_subscribed":
            return "This feed was already in your sources."
        default:
            return nil
        }
    }

    var isFeedSubscription: Bool {
        if submissionKind?.lowercased() == "feed_subscription" {
            return true
        }
        return detectedFeed != nil || feedSubscription != nil
    }

    var effectiveOutcome: String {
        if let outcome, !outcome.isEmpty {
            return outcome.lowercased()
        }
        switch status.lowercased() {
        case "new", "pending":
            return "queued"
        default:
            return status.lowercased()
        }
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
