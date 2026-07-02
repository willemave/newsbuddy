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
    let createdAt: Date

    var id: String { "\(status.rawValue)-\(ServerDate.format(createdAt))-\(note)" }

    enum CodingKeys: String, CodingKey {
        case status
        case note
        case createdAt = "created_at"
    }

    init(status: LearningDeckRunStatus, note: String, createdAt: Date) {
        self.status = status
        self.note = note
        self.createdAt = createdAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decode(LearningDeckRunStatus.self, forKey: .status)
        note = try container.decode(String.self, forKey: .note)
        let createdAtRaw = try container.decode(String.self, forKey: .createdAt)
        guard let createdAtParsed = ServerDate.parse(createdAtRaw) else {
            throw DecodingError.dataCorruptedError(
                forKey: .createdAt,
                in: container,
                debugDescription: "Unparseable date for createdAt"
            )
        }
        createdAt = createdAtParsed
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(status, forKey: .status)
        try container.encode(note, forKey: .note)
        try container.encode(ServerDate.format(createdAt), forKey: .createdAt)
    }
}

struct LearningDeckRun: Codable, Identifiable, Equatable {
    let id: Int
    let status: LearningDeckRunStatus
    let interestsPrompt: String?
    let timeline: [LearningDeckTimelineEntry]
    let errorMessage: String?
    let startedAt: Date?
    let completedAt: Date?
    let createdAt: Date
    let updatedAt: Date?

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

    init(
        id: Int,
        status: LearningDeckRunStatus,
        interestsPrompt: String?,
        timeline: [LearningDeckTimelineEntry],
        errorMessage: String?,
        startedAt: Date?,
        completedAt: Date?,
        createdAt: Date,
        updatedAt: Date?
    ) {
        self.id = id
        self.status = status
        self.interestsPrompt = interestsPrompt
        self.timeline = timeline
        self.errorMessage = errorMessage
        self.startedAt = startedAt
        self.completedAt = completedAt
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        status = try container.decode(LearningDeckRunStatus.self, forKey: .status)
        interestsPrompt = try container.decodeIfPresent(String.self, forKey: .interestsPrompt)
        timeline = try container.decode([LearningDeckTimelineEntry].self, forKey: .timeline)
        errorMessage = try container.decodeIfPresent(String.self, forKey: .errorMessage)
        startedAt = try Self.decodeOptionalDate(container, key: .startedAt)
        completedAt = try Self.decodeOptionalDate(container, key: .completedAt)
        let createdAtRaw = try container.decode(String.self, forKey: .createdAt)
        guard let createdAtParsed = ServerDate.parse(createdAtRaw) else {
            throw DecodingError.dataCorruptedError(
                forKey: .createdAt,
                in: container,
                debugDescription: "Unparseable date for createdAt"
            )
        }
        createdAt = createdAtParsed
        updatedAt = try Self.decodeOptionalDate(container, key: .updatedAt)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(status, forKey: .status)
        try container.encodeIfPresent(interestsPrompt, forKey: .interestsPrompt)
        try container.encode(timeline, forKey: .timeline)
        try container.encodeIfPresent(errorMessage, forKey: .errorMessage)
        try container.encodeIfPresent(startedAt.map(ServerDate.format), forKey: .startedAt)
        try container.encodeIfPresent(completedAt.map(ServerDate.format), forKey: .completedAt)
        try container.encode(ServerDate.format(createdAt), forKey: .createdAt)
        try container.encodeIfPresent(updatedAt.map(ServerDate.format), forKey: .updatedAt)
    }

    private static func decodeOptionalDate(
        _ container: KeyedDecodingContainer<CodingKeys>,
        key: CodingKeys
    ) throws -> Date? {
        guard let raw = try container.decodeIfPresent(String.self, forKey: key) else {
            return nil
        }
        guard let parsed = ServerDate.parse(raw) else {
            throw DecodingError.dataCorruptedError(
                forKey: key,
                in: container,
                debugDescription: "Unparseable date for \(key.stringValue)"
            )
        }
        return parsed
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
    let createdAt: Date
    let updatedAt: Date?

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

    init(
        id: Int,
        title: String,
        sourceKind: LearningDeckSourceKind,
        sourceURL: String?,
        sourceContentId: Int?,
        sourceTitle: String?,
        sourceMetadata: [String: AnyCodable],
        status: LearningDeckRunStatus?,
        shareEnabled: Bool,
        viewerAvailable: Bool,
        sourceNotesAvailable: Bool,
        latestSuccessfulRunId: Int?,
        latestRun: LearningDeckRun?,
        createdAt: Date,
        updatedAt: Date?
    ) {
        self.id = id
        self.title = title
        self.sourceKind = sourceKind
        self.sourceURL = sourceURL
        self.sourceContentId = sourceContentId
        self.sourceTitle = sourceTitle
        self.sourceMetadata = sourceMetadata
        self.status = status
        self.shareEnabled = shareEnabled
        self.viewerAvailable = viewerAvailable
        self.sourceNotesAvailable = sourceNotesAvailable
        self.latestSuccessfulRunId = latestSuccessfulRunId
        self.latestRun = latestRun
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        title = try container.decode(String.self, forKey: .title)
        sourceKind = try container.decode(LearningDeckSourceKind.self, forKey: .sourceKind)
        sourceURL = try container.decodeIfPresent(String.self, forKey: .sourceURL)
        sourceContentId = try container.decodeIfPresent(Int.self, forKey: .sourceContentId)
        sourceTitle = try container.decodeIfPresent(String.self, forKey: .sourceTitle)
        sourceMetadata = try container.decode([String: AnyCodable].self, forKey: .sourceMetadata)
        status = try container.decodeIfPresent(LearningDeckRunStatus.self, forKey: .status)
        shareEnabled = try container.decode(Bool.self, forKey: .shareEnabled)
        viewerAvailable = try container.decode(Bool.self, forKey: .viewerAvailable)
        sourceNotesAvailable = try container.decode(Bool.self, forKey: .sourceNotesAvailable)
        latestSuccessfulRunId = try container.decodeIfPresent(Int.self, forKey: .latestSuccessfulRunId)
        latestRun = try container.decodeIfPresent(LearningDeckRun.self, forKey: .latestRun)
        let createdAtRaw = try container.decode(String.self, forKey: .createdAt)
        guard let createdAtParsed = ServerDate.parse(createdAtRaw) else {
            throw DecodingError.dataCorruptedError(
                forKey: .createdAt,
                in: container,
                debugDescription: "Unparseable date for createdAt"
            )
        }
        createdAt = createdAtParsed
        if let updatedAtRaw = try container.decodeIfPresent(String.self, forKey: .updatedAt) {
            guard let updatedAtParsed = ServerDate.parse(updatedAtRaw) else {
                throw DecodingError.dataCorruptedError(
                    forKey: .updatedAt,
                    in: container,
                    debugDescription: "Unparseable date for updatedAt"
                )
            }
            updatedAt = updatedAtParsed
        } else {
            updatedAt = nil
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(title, forKey: .title)
        try container.encode(sourceKind, forKey: .sourceKind)
        try container.encodeIfPresent(sourceURL, forKey: .sourceURL)
        try container.encodeIfPresent(sourceContentId, forKey: .sourceContentId)
        try container.encodeIfPresent(sourceTitle, forKey: .sourceTitle)
        try container.encode(sourceMetadata, forKey: .sourceMetadata)
        try container.encodeIfPresent(status, forKey: .status)
        try container.encode(shareEnabled, forKey: .shareEnabled)
        try container.encode(viewerAvailable, forKey: .viewerAvailable)
        try container.encode(sourceNotesAvailable, forKey: .sourceNotesAvailable)
        try container.encodeIfPresent(latestSuccessfulRunId, forKey: .latestSuccessfulRunId)
        try container.encodeIfPresent(latestRun, forKey: .latestRun)
        try container.encode(ServerDate.format(createdAt), forKey: .createdAt)
        try container.encodeIfPresent(updatedAt.map(ServerDate.format), forKey: .updatedAt)
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
    let expiresAt: Date?

    enum CodingKeys: String, CodingKey {
        case url
        case expiresAt = "expires_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        url = try container.decode(String.self, forKey: .url)
        if let raw = try container.decodeIfPresent(String.self, forKey: .expiresAt) {
            guard let parsed = ServerDate.parse(raw) else {
                throw DecodingError.dataCorruptedError(
                    forKey: .expiresAt,
                    in: container,
                    debugDescription: "Unparseable date for expiresAt"
                )
            }
            expiresAt = parsed
        } else {
            expiresAt = nil
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(url, forKey: .url)
        try container.encodeIfPresent(expiresAt.map(ServerDate.format), forKey: .expiresAt)
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
