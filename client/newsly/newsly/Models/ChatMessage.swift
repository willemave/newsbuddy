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
