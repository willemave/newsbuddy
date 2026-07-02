//
//  ChatMessage.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation

extension APIChatMessageDisplayType {
    var sortOrder: Int {
        switch self {
        case .process_summary: 0
        case .message: 1
        case .unknown(_): 2
        }
    }
}

struct AssistantFeedOption: Codable, Identifiable, Equatable {
    let id: String
    let title: String
    let siteURL: String
    let feedURL: String
    let feedType: String
    let feedFormat: String
    let description: String?
    let rationale: String?
    let evidenceURL: String?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case siteURL = "site_url"
        case feedURL = "feed_url"
        case feedType = "feed_type"
        case feedFormat = "feed_format"
        case description
        case rationale
        case evidenceURL = "evidence_url"
    }

    init(
        id: String,
        title: String,
        siteURL: String,
        feedURL: String,
        feedType: String,
        feedFormat: String,
        description: String? = nil,
        rationale: String? = nil,
        evidenceURL: String? = nil
    ) {
        self.id = id
        self.title = title
        self.siteURL = siteURL
        self.feedURL = feedURL
        self.feedType = feedType
        self.feedFormat = feedFormat
        self.description = description
        self.rationale = rationale
        self.evidenceURL = evidenceURL
    }

    init(api response: APIAssistantFeedOption) {
        id = response.id
        title = response.title
        siteURL = response.siteUrl
        feedURL = response.feedUrl
        feedType = response.feedType.rawValue
        feedFormat = response.feedFormat.rawValue
        description = response.description
        rationale = response.rationale
        evidenceURL = response.evidenceUrl
    }

    var previewURLString: String {
        evidenceURL ?? siteURL
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
        guard let url = URL(string: siteURL), let host = url.host else {
            return siteURL
        }
        return host.replacingOccurrences(of: "www.", with: "")
    }

    var feedTypeLabel: String {
        switch feedType {
        case "substack":
            return "Substack"
        case "podcast_rss":
            return "Podcast"
        case "atom":
            return feedFormat == "atom" ? "Atom" : "RSS"
        default:
            return "Feed"
        }
    }

    var systemIcon: String {
        switch feedType {
        case "substack":
            return "newspaper"
        case "podcast_rss":
            return "waveform"
        default:
            return "dot.radiowaves.left.and.right"
        }
    }
}

struct CouncilCandidate: Codable, Identifiable, Equatable {
    let personaId: String
    let personaName: String
    let childSessionId: Int
    let content: String
    let status: String
    let order: Int

    var id: String { "\(personaId)-\(childSessionId)" }

    enum CodingKeys: String, CodingKey {
        case personaId = "persona_id"
        case personaName = "persona_name"
        case childSessionId = "child_session_id"
        case content
        case status
        case order
    }

    init(
        personaId: String,
        personaName: String,
        childSessionId: Int,
        content: String,
        status: String,
        order: Int
    ) {
        self.personaId = personaId
        self.personaName = personaName
        self.childSessionId = childSessionId
        self.content = content
        self.status = status
        self.order = order
    }

    init(api response: APICouncilCandidate) {
        personaId = response.personaId
        personaName = response.personaName
        childSessionId = response.childSessionId
        content = response.content
        status = response.status
        order = response.order
    }
}

/// Individual message in a chat session
struct ChatMessage: Codable, Identifiable, Equatable {
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
    let councilCandidates: [CouncilCandidate]
    let activeCouncilChildSessionId: Int?

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
        feedOptions: [AssistantFeedOption] = [],
        councilCandidates: [CouncilCandidate] = [],
        activeCouncilChildSessionId: Int? = nil
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
        self.councilCandidates = councilCandidates
        self.activeCouncilChildSessionId = activeCouncilChildSessionId
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
            feedOptions: response.feedOptions.map(AssistantFeedOption.init(api:)),
            councilCandidates: response.councilCandidates.map(CouncilCandidate.init(api:)),
            activeCouncilChildSessionId: response.activeCouncilChildSessionId
        )
    }

    init(from decoder: Decoder) throws {
        self.init(api: try APIChatMessage(from: decoder))
    }

    // Decoding goes through the generated APIChatMessage, which parses the wire
    // timestamp string into Date. Encode must stay symmetric (Date back to the
    // canonical string) — synthesized Encodable would emit a numeric timestamp.
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encodeIfPresent(sourceMessageId, forKey: .sourceMessageId)
        try container.encodeIfPresent(displayKey, forKey: .displayKey)
        try container.encode(role, forKey: .role)
        try container.encode(ServerDate.format(timestamp), forKey: .timestamp)
        try container.encode(content, forKey: .content)
        try container.encode(displayType, forKey: .displayType)
        try container.encodeIfPresent(processLabel, forKey: .processLabel)
        try container.encodeIfPresent(status, forKey: .status)
        try container.encodeIfPresent(error, forKey: .error)
        try container.encode(feedOptions, forKey: .feedOptions)
        try container.encode(councilCandidates, forKey: .councilCandidates)
        try container.encodeIfPresent(activeCouncilChildSessionId, forKey: .activeCouncilChildSessionId)
    }

    enum CodingKeys: String, CodingKey {
        case id, role, timestamp, content, status, error
        case sourceMessageId = "source_message_id"
        case displayKey = "display_key"
        case displayType = "display_type"
        case processLabel = "process_label"
        case feedOptions = "feed_options"
        case councilCandidates = "council_candidates"
        case activeCouncilChildSessionId = "active_council_child_session_id"
    }

    var isProcessing: Bool {
        status == .processing
    }

    var hasFailed: Bool {
        status == .failed
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

    var hasCouncilCandidates: Bool {
        !councilCandidates.isEmpty
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
