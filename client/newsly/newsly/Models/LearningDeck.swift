//
//  LearningDeck.swift
//  newsly
//

import Foundation

enum LearningDeckSourceKind: Equatable {
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

enum LearningDeckRunStatus: Equatable {
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

struct LearningDeckTimelineEntry: Identifiable, Equatable {
    let status: LearningDeckRunStatus
    let note: String
    let createdAt: Date

    var id: String { "\(status.rawValue)-\(ServerDate.format(createdAt))-\(note)" }

    init(status: LearningDeckRunStatus, note: String, createdAt: Date) {
        self.status = status
        self.note = note
        self.createdAt = createdAt
    }

}

struct LearningDeckRun: Identifiable, Equatable {
    let id: Int
    let status: LearningDeckRunStatus
    let interestsPrompt: String?
    let timeline: [LearningDeckTimelineEntry]
    let errorMessage: String?
    let startedAt: Date?
    let completedAt: Date?
    let createdAt: Date
    let updatedAt: Date?

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

}

struct LearningDeck: Identifiable {
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

struct LearningDeckListResponse {
    let decks: [LearningDeck]
}

struct LearningDeckShareResponse {
    let shareEnabled: Bool
    let shareURL: String?
}
