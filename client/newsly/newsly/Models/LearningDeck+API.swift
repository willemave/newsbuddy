//
//  LearningDeck+API.swift
//  newsly
//

import Foundation

extension LearningDeckSourceKind {
    init(apiValue: APILearningDeckSourceKind) {
        self.init(rawValue: apiValue.rawValue)
    }
}

extension LearningDeckRunStatus {
    init(apiValue: APILearningDeckRunStatus) {
        self.init(rawValue: apiValue.rawValue)
    }

    init(apiValue: APILearningDeckStatus) {
        self.init(rawValue: apiValue.rawValue)
    }
}

extension LearningDeckTimelineEntry {
    init(apiResponse: APILearningDeckTimelineEntry) {
        self.init(
            status: LearningDeckRunStatus(apiValue: apiResponse.status),
            note: apiResponse.note,
            createdAt: apiResponse.createdAt
        )
    }
}

extension LearningDeckRun {
    init(apiResponse: APILearningDeckRunResponse) {
        self.init(
            id: apiResponse.id,
            status: LearningDeckRunStatus(apiValue: apiResponse.status),
            interestsPrompt: apiResponse.interestsPrompt,
            timeline: apiResponse.timeline.map(LearningDeckTimelineEntry.init(apiResponse:)),
            errorMessage: apiResponse.errorMessage,
            startedAt: apiResponse.startedAt,
            completedAt: apiResponse.completedAt,
            createdAt: apiResponse.createdAt,
            updatedAt: apiResponse.updatedAt
        )
    }
}

extension LearningDeck {
    init(apiResponse: APILearningDeckResponse) {
        self.init(
            id: apiResponse.id,
            title: apiResponse.title,
            sourceKind: LearningDeckSourceKind(apiValue: apiResponse.sourceKind),
            sourceURL: apiResponse.sourceUrl,
            sourceContentId: apiResponse.sourceContentId,
            sourceTitle: apiResponse.sourceTitle,
            sourceMetadata: apiResponse.sourceMetadata,
            status: apiResponse.status.map(LearningDeckRunStatus.init(apiValue:)),
            shareEnabled: apiResponse.shareEnabled,
            viewerAvailable: apiResponse.viewerAvailable,
            sourceNotesAvailable: apiResponse.sourceNotesAvailable,
            thumbnailURL: apiResponse.thumbnailUrl,
            latestSuccessfulRunId: apiResponse.latestSuccessfulRunId,
            latestRun: apiResponse.latestRun.map(LearningDeckRun.init(apiResponse:)),
            createdAt: apiResponse.createdAt,
            updatedAt: apiResponse.updatedAt
        )
    }
}

extension LearningDeckShareResponse {
    init(apiResponse: APILearningDeckShareResponse) {
        self.init(
            shareEnabled: apiResponse.shareEnabled,
            shareURL: apiResponse.shareUrl
        )
    }
}
