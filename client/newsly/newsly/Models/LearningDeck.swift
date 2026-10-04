//
//  LearningDeck.swift
//  newsly
//

import Foundation

typealias LearningDeckSourceKind = APILearningDeckSourceKind
typealias LearningDeckRunStatus = APILearningDeckStatus

extension APILearningDeckStatus {
    var isActive: Bool {
        switch self {
        case .queued, .preparing, .generating, .validating, .publishing:
            true
        case .completed, .failed, .cancelled, .ready, .unknown:
            false
        }
    }

    var displayLabel: String {
        switch self {
        case .queued: "Queued"
        case .preparing: "Preparing"
        case .generating: "Generating"
        case .validating: "Checking"
        case .publishing: "Publishing"
        case .completed, .ready: "Ready"
        case .failed: "Failed"
        case .cancelled: "Cancelled"
        case .unknown: "Pending"
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
    let thumbnailURL: String?
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
        thumbnailURL: String? = nil,
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
        self.thumbnailURL = thumbnailURL
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

    var thumbnailCacheIdentifier: String? {
        guard thumbnailURL != nil, let latestSuccessfulRunId else { return nil }
        return "learning-deck:\(id):attempt:\(latestSuccessfulRunId)"
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
