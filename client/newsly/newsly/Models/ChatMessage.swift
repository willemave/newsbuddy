//
//  ChatMessage.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation

typealias AssistantFeedOption = APIAssistantFeedOption

extension APIAssistantFeedOption: Identifiable, Equatable {
    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.id == rhs.id
            && lhs.title == rhs.title
            && lhs.siteUrl == rhs.siteUrl
            && lhs.feedUrl == rhs.feedUrl
            && lhs.feedType == rhs.feedType
            && lhs.feedFormat == rhs.feedFormat
            && lhs.description == rhs.description
            && lhs.rationale == rhs.rationale
            && lhs.evidenceUrl == rhs.evidenceUrl
            && lhs.isSubscribed == rhs.isSubscribed
            && lhs.publishingActivity == rhs.publishingActivity
    }

    var previewURLString: String {
        evidenceUrl ?? siteUrl
    }

    var subtitleText: String? {
        if let rationale, !rationale.isEmpty {
            return rationale
        }
        if let description, !description.isEmpty {
            return description
        }
        return nil
    }

    var hostLabel: String {
        guard let url = URL(string: siteUrl), let host = url.host else {
            return siteUrl
        }
        return host.replacingOccurrences(of: "www.", with: "")
    }

    var feedTypeLabel: String {
        switch feedType {
        case .substack:
            return "Substack"
        case .podcast_rss:
            return "Podcast"
        case .aggregator:
            return "Aggregator"
        case .atom:
            return feedFormat == .atom ? "Atom" : "RSS"
        default:
            return "Feed"
        }
    }

    var systemIcon: String {
        switch feedType {
        case .substack:
            return "newspaper"
        case .podcast_rss:
            return "waveform"
        case .aggregator:
            return onboardingAggregatorOptions
                .first { "aggregator://\($0.key)" == feedUrl }?
                .icon ?? "square.stack.3d.up"
        default:
            return "dot.radiowaves.left.and.right"
        }
    }
}

extension APIAssistantPublishedItem: Equatable {
    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.title == rhs.title && lhs.url == rhs.url
            && lhs.publishedAt == rhs.publishedAt && lhs.collectedAt == rhs.collectedAt
    }
}

extension APIAssistantSourceActivity: Equatable {
    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.basis == rhs.basis && lhs.checkedAt == rhs.checkedAt
            && lhs.windowStart == rhs.windowStart && lhs.yearWindowStart == rhs.yearWindowStart
            && lhs.windowEnd == rhs.windowEnd && lhs.latestItem == rhs.latestItem
            && lhs.observedCount == rhs.observedCount && lhs.observedYearCount == rhs.observedYearCount
            && lhs.undatedCount == rhs.undatedCount
    }
}

/// Keeps observed source activity distinct from a publisher's complete history.
struct AssistantSourceActivityPresentation {
    let activity: APIAssistantSourceActivity?

    var latestTitle: String? {
        guard let title = activity?.latestItem?.title?.trimmingCharacters(in: .whitespacesAndNewlines),
              !title.isEmpty else { return nil }
        return title
    }

    var latestDisplayTitle: String? {
        guard activity?.latestItem != nil else { return nil }
        return latestTitle ?? "Latest observed item"
    }

    var latestURL: URL? {
        guard let value = activity?.latestItem?.url, let url = URL(string: value),
              ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
              let host = url.host, !host.isEmpty else { return nil }
        return url
    }

    var latestDate: Date? {
        activity?.latestItem?.publishedAt ?? activity?.latestItem?.collectedAt
    }

    var latestDateLabel: String {
        if activity?.latestItem?.publishedAt != nil { return "Latest observed publication" }
        if activity?.latestItem?.collectedAt != nil { return "Latest collected by Newsly" }
        if activity?.basis == .newsly_corpus { return "Latest collection date unavailable" }
        return "Latest publication date unavailable"
    }

    var quarterCountText: String { countText(activity?.observedCount, period: "90 days") }
    var yearCountText: String { countText(activity?.observedYearCount, period: "365 days") }

    var evidenceText: String? {
        guard let activity else { return nil }
        switch activity.basis {
        case .feed_snapshot:
            return "Published items in the current feed; history may be incomplete."
        case .newsly_corpus:
            return "Items collected by Newsly; publisher totals may differ."
        default:
            return "Observed history; complete publishing totals are unavailable."
        }
    }

    var undatedText: String? {
        guard let count = activity?.undatedCount, count > 0 else { return nil }
        return "\(count) undated \(count == 1 ? "item" : "items") excluded from counts."
    }

    private func countText(_ count: Int?, period: String) -> String {
        guard let count else { return "\(period): count unavailable" }
        return "\(period): \(count) observed"
    }
}

/// Individual message in a chat session
struct ChatMessage: Identifiable, Equatable {
    let id: Int
    let sourceMessageId: Int?
    let displayKey: String?
    let role: APIChatMessageRole
    let timestamp: Date
    let content: String
    let displayType: APIChatMessageDisplayType
    let processLabel: String?
    let status: APIMessageProcessingStatus?
    let error: String?
    let feedOptions: [AssistantFeedOption]

    init(
        id: Int,
        sourceMessageId: Int? = nil,
        displayKey: String? = nil,
        role: APIChatMessageRole,
        timestamp: Date,
        content: String,
        displayType: APIChatMessageDisplayType = .message,
        processLabel: String? = nil,
        status: APIMessageProcessingStatus? = nil,
        error: String? = nil,
        feedOptions: [AssistantFeedOption] = []
    ) {
        self.id = id
        self.sourceMessageId = sourceMessageId
        self.displayKey = displayKey
        self.role = role
        self.timestamp = timestamp
        self.content = content
        self.displayType = displayType
        self.processLabel = processLabel
        self.status = status
        self.error = error
        self.feedOptions = feedOptions
    }

    init(api response: APIChatMessage) {
        self.init(
            id: response.id,
            sourceMessageId: response.sourceMessageId,
            displayKey: response.displayKey.isEmpty ? nil : response.displayKey,
            role: response.role,
            timestamp: response.timestamp,
            content: response.content,
            displayType: response.displayType,
            processLabel: response.processLabel,
            status: response.status,
            error: response.error,
            feedOptions: response.feedOptions
        )
    }

    var isProcessing: Bool {
        status == .processing
    }

    var hasFailed: Bool {
        status == .failed
    }

    func failed(error: String) -> Self {
        Self(
            id: id,
            sourceMessageId: sourceMessageId,
            displayKey: displayKey,
            role: role,
            timestamp: timestamp,
            content: content,
            displayType: displayType,
            processLabel: processLabel,
            status: .failed,
            error: error,
            feedOptions: feedOptions
        )
    }

    var formattedTime: String {
        ChatMessageTimestampFormatter.formattedTime(from: timestamp)
    }

    var isUser: Bool {
        role == .user
    }

    var isAssistant: Bool {
        role == .assistant
    }

    var isVisiblePartialResponse: Bool {
        isAssistant
            && isProcessing
            && !content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var isProcessSummary: Bool {
        displayType == .process_summary
    }

    var processSummaryText: String {
        processLabel ?? content
    }

    var processSummaryDetail: String? {
        let trimmedContent = content.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedLabel = processSummaryText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedContent.isEmpty, trimmedContent != trimmedLabel else { return nil }
        return trimmedContent
    }

    var hasFeedOptions: Bool {
        !feedOptions.isEmpty
    }

}

private enum ChatMessageTimestampFormatter {
    private static let displayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.timeStyle = .short
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    static func formattedTime(from timestamp: Date) -> String {
        displayFormatter.string(from: timestamp)
    }
}
