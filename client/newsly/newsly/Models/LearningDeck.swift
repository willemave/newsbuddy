//
//  LearningDeck.swift
//  newsly
//

import Foundation

enum LearningDeckSourceKind: Equatable, Codable {
    case content
    case githubRepo
    case unknown(String)

    var rawValue: String {
        switch self {
        case .content:
            return "content"
        case .githubRepo:
            return "github_repo"
        case .unknown(let value):
            return value
        }
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        let rawValue = try container.decode(String.self)
        self = Self(rawValue: rawValue)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }

    init(rawValue: String) {
        switch rawValue {
        case "content":
            self = .content
        case "github_repo":
            self = .githubRepo
        default:
            self = .unknown(rawValue)
        }
    }
}

enum LearningDeckRunStatus: Equatable, Codable {
    case queued
    case preparing
    case generating
    case validating
    case publishing
    case completed
    case failed
    case cancelled
    case ready
    case unknown(String)

    var rawValue: String {
        switch self {
        case .queued:
            return "queued"
        case .preparing:
            return "preparing"
        case .generating:
            return "generating"
        case .validating:
            return "validating"
        case .publishing:
            return "publishing"
        case .completed:
            return "completed"
        case .failed:
            return "failed"
        case .cancelled:
            return "cancelled"
        case .ready:
            return "ready"
        case .unknown(let value):
            return value
        }
    }

    var isActive: Bool {
        switch self {
        case .queued, .preparing, .generating, .validating, .publishing:
            return true
        case .completed, .failed, .cancelled, .ready, .unknown:
            return false
        }
    }

    var displayLabel: String {
        switch self {
        case .queued:
            return "Queued"
        case .preparing:
            return "Preparing"
        case .generating:
            return "Generating"
        case .validating:
            return "Checking"
        case .publishing:
            return "Publishing"
        case .completed, .ready:
            return "Ready"
        case .failed:
            return "Failed"
        case .cancelled:
            return "Cancelled"
        case .unknown:
            return "Pending"
        }
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        let rawValue = try container.decode(String.self)
        self = Self(rawValue: rawValue)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }

    init(rawValue: String) {
        switch rawValue {
        case "queued":
            self = .queued
        case "preparing":
            self = .preparing
        case "generating":
            self = .generating
        case "validating":
            self = .validating
        case "publishing":
            self = .publishing
        case "completed":
            self = .completed
        case "failed":
            self = .failed
        case "cancelled":
            self = .cancelled
        case "ready":
            self = .ready
        default:
            self = .unknown(rawValue)
        }
    }
}

struct LearningDeckCreateRequest: Codable {
    let contentId: Int?
    let newsItemId: Int?
    let url: String?
    let interestsPrompt: String?

    enum CodingKeys: String, CodingKey {
        case contentId = "content_id"
        case newsItemId = "news_item_id"
        case url
        case interestsPrompt = "interests_prompt"
    }
}

struct LearningDeckTimelineEntry: Codable, Identifiable, Equatable {
    let status: LearningDeckRunStatus
    let note: String
    let createdAt: String

    var id: String { "\(status.rawValue)-\(createdAt)-\(note)" }

    enum CodingKeys: String, CodingKey {
        case status
        case note
        case createdAt = "created_at"
    }
}

struct LearningDeckRun: Codable, Identifiable, Equatable {
    let id: Int
    let status: LearningDeckRunStatus
    let interestsPrompt: String?
    let timeline: [LearningDeckTimelineEntry]
    let errorMessage: String?
    let startedAt: String?
    let completedAt: String?
    let createdAt: String
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case status
        case interestsPrompt = "interests_prompt"
        case timeline
        case errorMessage = "error_message"
        case startedAt = "started_at"
        case completedAt = "completed_at"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct LearningDeck: Codable, Identifiable {
    let id: Int
    let title: String
    let sourceKind: LearningDeckSourceKind
    let sourceURL: String?
    let sourceContentId: Int?
    let sourceTitle: String?
    let sourceMetadata: [String: AnyCodable]
    let status: LearningDeckRunStatus?
    let shareEnabled: Bool
    let viewerAvailable: Bool
    let sourceNotesAvailable: Bool
    let latestSuccessfulRunId: Int?
    let latestRun: LearningDeckRun?
    let createdAt: String
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case sourceKind = "source_kind"
        case sourceURL = "source_url"
        case sourceContentId = "source_content_id"
        case sourceTitle = "source_title"
        case sourceMetadata = "source_metadata"
        case status
        case shareEnabled = "share_enabled"
        case viewerAvailable = "viewer_available"
        case sourceNotesAvailable = "source_notes_available"
        case latestSuccessfulRunId = "latest_successful_run_id"
        case latestRun = "latest_run"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    var displayTitle: String {
        nonEmptyTrimmed(title) ?? nonEmptyTrimmed(sourceTitle) ?? "Learning Deck"
    }

    var statusLabel: String {
        if hasActiveLatestRun {
            return latestRun?.status.displayLabel ?? "Pending"
        }
        if viewerAvailable {
            return "Ready"
        }
        return status?.displayLabel ?? "Pending"
    }

    var hasActiveLatestRun: Bool {
        latestRun?.status.isActive == true
    }

    var latestNote: String? {
        if let errorMessage = nonEmptyTrimmed(latestRun?.errorMessage) {
            return errorMessage
        }
        return latestRun?.timeline.last?.note
    }
}

struct LearningDeckListResponse: Codable {
    let decks: [LearningDeck]
}

struct LearningDeckURLResponse: Codable {
    let url: String
    let expiresAt: String?

    enum CodingKeys: String, CodingKey {
        case url
        case expiresAt = "expires_at"
    }
}

struct LearningDeckShareResponse: Codable {
    let shareEnabled: Bool
    let shareURL: String?

    enum CodingKeys: String, CodingKey {
        case shareEnabled = "share_enabled"
        case shareURL = "share_url"
    }
}
