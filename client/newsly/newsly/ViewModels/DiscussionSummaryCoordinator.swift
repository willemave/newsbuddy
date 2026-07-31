//
//  DiscussionSummaryCoordinator.swift
//  newsly
//

import Foundation
import Observation
import os.log

private let discussionSummaryLogger = Logger(subsystem: "com.newsly", category: "DiscussionSummary")

protocol ContentDiscussionServicing: AnyObject {
    func fetchContentDiscussion(id: Int, contentType: APIContentType?) async throws -> ContentDiscussion
}

extension ContentService: ContentDiscussionServicing {}

@MainActor
@Observable
final class DiscussionSummaryCoordinator {
    private(set) var payload: ContentDiscussion?

    private var requestToken = UUID()
    private let contentService: any ContentDiscussionServicing

    init(contentService: any ContentDiscussionServicing) {
        self.contentService = contentService
    }

    func reset() {
        requestToken = UUID()
        payload = nil
    }

    func inlineSummaryPayload(for content: ContentDetail) -> ContentDiscussion? {
        guard let payload,
              payload.contentId == content.id,
              payload.summary != nil else {
            return nil
        }
        return payload
    }

    func loadStoredSummary(
        for content: ContentDetail,
        currentContentId: Int?
    ) async {
        if inlineSummaryPayload(for: content) != nil {
            return
        }

        let token = UUID()
        requestToken = token

        do {
            let discussion = try await contentService.fetchContentDiscussion(
                id: content.id,
                contentType: content.contentType
            )
            guard requestToken == token,
                  currentContentId == content.id,
                  discussion.summary != nil else {
                return
            }
            payload = discussion
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            discussionSummaryLogger.debug(
                "Stored discussion summary load failed | contentId=\(content.id) error=\(error.localizedDescription)"
            )
        }
    }
}
