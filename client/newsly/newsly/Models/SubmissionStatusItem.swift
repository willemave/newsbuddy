//
//  SubmissionStatusItem.swift
//  newsly
//
//  Created by Assistant on 1/14/26.
//

import Foundation

/// Domain-level view of a feed subscription outcome attached to a submission status row.
/// Wire decoding lives on the generated `APISubmissionFeedSubscriptionResponse`; this type
/// mirrors it 1:1 today but keeps the domain layer decoupled from the generated name.
struct SubmissionFeedSubscription {
    let status: String
    let feedUrl: String?
    let feedType: String?
    let created: Bool?
    let configId: Int?
    let initialDownload: SubmissionFeedInitialDownload?

    init(api response: APISubmissionFeedSubscriptionResponse) {
        status = response.status
        feedUrl = response.feedUrl
        feedType = response.feedType
        created = response.created
        configId = response.configId
        initialDownload = response.initialDownload.map(SubmissionFeedInitialDownload.init(api:))
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

/// Domain-level view of the initial download result for a newly subscribed feed.
struct SubmissionFeedInitialDownload {
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

    init(api response: APISubmissionFeedInitialDownloadResponse) {
        requestedCount = response.requestedCount
        ran = response.ran
        status = response.status
        reason = response.reason
        error = response.error
        configId = response.configId
        baseLimit = response.baseLimit
        targetLimit = response.targetLimit
        scraped = response.scraped
        saved = response.saved
        duplicates = response.duplicates
        errors = response.errors
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

/// Domain-level view of a user-submitted content item's processing status, built from
/// the generated `APISubmissionStatusResponse` wire model (mirrors the `ContentDetail`
/// wire/domain split). `createdAt`/`processedAt` stay `String` because the backend DTO
/// fields are plain `str`, not `UTCDateTime` (see `SubmissionStatusResponse` in
/// The generated boundary decodes server-owned timestamps as `Date`; this
/// presentation model keeps canonical strings for its existing formatting API.
struct SubmissionStatusItem: Identifiable {
    let id: Int
    let contentType: APIContentType
    let url: String
    let sourceUrl: String?
    let title: String?
    let status: APIContentStatus
    let errorMessage: String?
    let createdAt: String
    let processedAt: String?
    let submittedVia: String?
    let isSelfSubmission: Bool
    let submissionKind: APISubmissionKind
    let outcome: APISubmissionOutcome
    let rationale: String?
    let detectedFeed: DetectedFeed?
    let feedSubscription: SubmissionFeedSubscription?

    init(api response: APISubmissionStatusResponse) {
        id = response.id
        contentType = response.contentType
        url = response.url
        sourceUrl = response.sourceUrl
        title = response.title
        status = response.status
        errorMessage = response.errorMessage
        createdAt = ServerDate.format(response.createdAt)
        processedAt = response.processedAt.map(ServerDate.format)
        submittedVia = response.submittedVia
        isSelfSubmission = response.isSelfSubmission
        switch response.result {
        case let .content(result):
            submissionKind = .content
            outcome = result.outcome
            rationale = nil
            detectedFeed = nil
            feedSubscription = nil
        case let .feed_subscription(result):
            submissionKind = .feed_subscription
            outcome = result.outcome
            rationale = nil
            detectedFeed = Self.detectedFeed(from: result.detectedFeed)
            feedSubscription = result.subscription.map(SubmissionFeedSubscription.init(api:))
        case let .learning_deck(result):
            submissionKind = .learning_deck
            outcome = result.outcome
            rationale = nil
            detectedFeed = nil
            feedSubscription = nil
        case let .no_action(result):
            submissionKind = .content
            outcome = .no_action
            rationale = result.rationale
            detectedFeed = nil
            feedSubscription = nil
        case .unknown:
            // During the compatibility window the server keeps these legacy mirrors so a newer
            // result tag can still receive a useful, bounded presentation in an older client.
            submissionKind = response.submissionKind
            outcome = response.outcome
            rationale = response.rationale
            detectedFeed = Self.detectedFeed(from: response.detectedFeed)
            feedSubscription = response.feedSubscription.map(SubmissionFeedSubscription.init(api:))
        }
    }

    init(
        id: Int,
        contentType: APIContentType,
        url: String,
        sourceUrl: String?,
        title: String?,
        status: APIContentStatus,
        errorMessage: String?,
        createdAt: String,
        processedAt: String?,
        submittedVia: String?,
        isSelfSubmission: Bool,
        submissionKind: APISubmissionKind = .content,
        outcome: APISubmissionOutcome = .processing,
        rationale: String? = nil,
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
        self.rationale = rationale
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
        case .queued:
            return "Queued"
        case .processing:
            return "Processing"
        case .completed:
            return "Completed"
        case .no_action:
            return "No action taken"
        case .failed:
            return "Failed"
        case .skipped:
            return "Skipped"
        case .subscribed:
            return "Subscribed"
        case .already_subscribed:
            return "Already subscribed"
        case .feed_not_found:
            return "Feed not found"
        case .feed_fetch_failed:
            return "Couldn't check feed"
        case .feed_subscription_failed:
            return "Couldn't add feed"
        case .unknown(let rawValue):
            return rawValue.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    var isError: Bool {
        switch effectiveOutcome {
        case .failed, .skipped, .feed_not_found, .feed_fetch_failed, .feed_subscription_failed:
            return true
        default:
            return false
        }
    }

    var errorDisplayText: String? {
        guard isError else { return nil }
        switch effectiveOutcome {
        case .skipped:
            return "Processing was skipped."
        case .feed_not_found:
            return "No RSS or Atom feed was found for this URL."
        case .feed_fetch_failed:
            return "Newsbuddy couldn't check this page for feeds. Try submitting it again."
        case .feed_subscription_failed:
            return "Newsbuddy couldn't add this feed. Try submitting it again."
        default:
            return "Newsbuddy couldn't finish processing this item. Try submitting it again."
        }
    }

    var statusDetailText: String? {
        if let errorDisplayText {
            return errorDisplayText
        }
        if effectiveOutcome == .no_action {
            return rationale ?? "Newsbuddy could not find an action to take for this link."
        }
        if isLearningDeck {
            switch effectiveOutcome {
            case .completed:
                return "Learning Deck is ready."
            case .processing:
                return "Learning Deck is being created."
            case .queued:
                return "Learning Deck is queued."
            default:
                return nil
            }
        }
        guard isFeedSubscription else { return nil }
        switch effectiveOutcome {
        case .subscribed:
            if feedSubscription?.initialDownload?.status?.lowercased() == "failed" {
                return "Feed added, but recent items could not be downloaded."
            }
            if let saved = feedSubscription?.initialDownload?.saved, saved > 0 {
                return "Feed added; \(saved) recent item\(saved == 1 ? "" : "s") saved."
            }
            return "Feed added."
        case .already_subscribed:
            return "This feed was already in your sources."
        default:
            return nil
        }
    }

    var isFeedSubscription: Bool {
        if submissionKind == .feed_subscription {
            return true
        }
        return detectedFeed != nil || feedSubscription != nil
    }

    var isLearningDeck: Bool {
        submissionKind == .learning_deck
    }

    var typeDisplay: String {
        if isLearningDeck {
            return "Learning Deck"
        }
        if isFeedSubscription {
            return "Feed Subscription"
        }
        return contentType.displayName
    }

    /// The submission outcome to drive display logic from. The wire model's `outcome`
    /// field is required and always populated by the backend (default `.processing`),
    /// so this is a straight passthrough — kept as a computed property to preserve the
    /// call-site name used throughout the view layer.
    var effectiveOutcome: APISubmissionOutcome {
        outcome
    }

    var recoveryURL: URL? {
        let canRecover: Bool
        if effectiveOutcome == .no_action {
            canRecover = true
        } else {
            canRecover = isSelfSubmission && isError
        }

        guard canRecover,
              let candidate = URL(string: url),
              let scheme = candidate.scheme?.lowercased(),
              scheme == "http" || scheme == "https" else {
            return nil
        }
        return candidate
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
        ServerDate.parse(dateString)
    }

    private static func detectedFeed(from response: APIDetectedFeed?) -> DetectedFeed? {
        response.map {
            DetectedFeed(url: $0.url, type: $0.type, title: $0.title, format: $0.format)
        }
    }
}

/// A page of submission status rows, mapped from the generated
/// `APISubmissionStatusListResponse`. Named distinctly from the generated type (rather
/// than mirroring it as `SubmissionStatusListResponse`) so it does not re-introduce a
/// hand-rolled-name collision on the contracts allowlist.
struct SubmissionStatusFeed {
    let submissions: [SubmissionStatusItem]
    let meta: PaginationMetadata

    init(submissions: [SubmissionStatusItem], meta: PaginationMetadata) {
        self.submissions = submissions
        self.meta = meta
    }

    init(api response: APISubmissionStatusListResponse) {
        submissions = response.submissions.map(SubmissionStatusItem.init(api:))
        meta = response.meta
    }

    var nextCursor: String? { meta.nextCursor }
    var hasMore: Bool { meta.hasMore }
    var pageSize: Int { meta.pageSize }
}
