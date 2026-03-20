//
//  ChatMessage.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation

/// Role of a chat message sender
enum ChatMessageRole: String, Codable {
    case user
    case assistant
    case system
    case tool
}

enum ChatMessageDisplayType: String, Codable {
    case message
    case processSummary = "process_summary"
}

/// Processing status for async chat messages
enum MessageProcessingStatus: String, Codable {
    case processing
    case completed
    case failed
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

/// Individual message in a chat session
struct ChatMessage: Codable, Identifiable {
    let id: Int
    let role: ChatMessageRole
    let timestamp: String
    let content: String
    let displayType: ChatMessageDisplayType
    let processLabel: String?
    let status: MessageProcessingStatus?
    let error: String?
    let feedOptions: [AssistantFeedOption]

    // Allow status to be optional (default to completed for backward compatibility)
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        role = try container.decode(ChatMessageRole.self, forKey: .role)
        timestamp = try container.decode(String.self, forKey: .timestamp)
        content = try container.decode(String.self, forKey: .content)
        displayType =
            try container.decodeIfPresent(ChatMessageDisplayType.self, forKey: .displayType)
            ?? .message
        processLabel = try container.decodeIfPresent(String.self, forKey: .processLabel)
        status = try container.decodeIfPresent(MessageProcessingStatus.self, forKey: .status)
        error = try container.decodeIfPresent(String.self, forKey: .error)
        feedOptions = try container.decodeIfPresent([AssistantFeedOption].self, forKey: .feedOptions) ?? []
    }

    init(
        id: Int,
        role: ChatMessageRole,
        timestamp: String,
        content: String,
        displayType: ChatMessageDisplayType = .message,
        processLabel: String? = nil,
        status: MessageProcessingStatus? = nil,
        error: String? = nil,
        feedOptions: [AssistantFeedOption] = []
    ) {
        self.id = id
        self.role = role
        self.timestamp = timestamp
        self.content = content
        self.displayType = displayType
        self.processLabel = processLabel
        self.status = status
        self.error = error
        self.feedOptions = feedOptions
    }

    enum CodingKeys: String, CodingKey {
        case id, role, timestamp, content, status, error
        case displayType = "display_type"
        case processLabel = "process_label"
        case feedOptions = "feed_options"
    }

    var isProcessing: Bool {
        status == .processing
    }

    var hasFailed: Bool {
        status == .failed
    }

    var formattedTime: String {
        // Try ISO8601 with fractional seconds
        let iso8601WithFractional = ISO8601DateFormatter()
        iso8601WithFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var date = iso8601WithFractional.date(from: timestamp)

        // Try ISO8601 without fractional seconds
        if date == nil {
            let iso8601 = ISO8601DateFormatter()
            iso8601.formatOptions = [.withInternetDateTime]
            date = iso8601.date(from: timestamp)
        }

        // Try basic ISO format
        if date == nil {
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
            formatter.timeZone = TimeZone(abbreviation: "UTC")
            date = formatter.date(from: timestamp)
        }

        guard let date = date else {
            return ""
        }

        let displayFormatter = DateFormatter()
        displayFormatter.timeStyle = .short
        displayFormatter.timeZone = TimeZone.current
        return displayFormatter.string(from: date)
    }

    var isUser: Bool {
        role == .user
    }

    var isAssistant: Bool {
        role == .assistant
    }

    var isProcessSummary: Bool {
        displayType == .processSummary
    }

    var processSummaryText: String {
        processLabel ?? content
    }

    var hasFeedOptions: Bool {
        !feedOptions.isEmpty
    }
}
