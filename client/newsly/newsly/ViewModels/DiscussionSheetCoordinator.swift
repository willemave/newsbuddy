//
//  DiscussionSheetCoordinator.swift
//  newsly
//

import Foundation
import Observation
import os.log

private let discussionSheetLogger = Logger(subsystem: "com.newsly", category: "DiscussionSheet")

protocol ContentDiscussionServicing: AnyObject {
    func refreshContentDiscussion(id: Int, contentType: APIContentType?) async throws -> ContentDiscussion
    func fetchContentDiscussion(id: Int, contentType: APIContentType?) async throws -> ContentDiscussion
}

extension ContentService: ContentDiscussionServicing {}

@MainActor
@Observable
final class DiscussionSheetCoordinator {
    var payload: ContentDiscussion?
    var isLoading = false
    var fallbackURL: URL?
    var unavailableMessage: String?
    var selectedTab: DiscussionTab = .comments
    var collapsedCommentIDs: Set<String> = []

    private var requestToken = UUID()
    private let contentService: any ContentDiscussionServicing

    init(contentService: any ContentDiscussionServicing) {
        self.contentService = contentService
    }

    var resolvedFallbackURL: URL? {
        if let discussionURL = payload?.discussionURL,
           let url = URL(string: discussionURL) {
            return url
        }
        return fallbackURL
    }

    var unavailableText: String {
        if let unavailableMessage {
            return unavailableMessage
        }
        if let payload {
            return payload.unavailableMessage
        }
        return "No discussion is available for this story."
    }

    func prepareForPresentation(fallbackURL: URL) {
        self.fallbackURL = fallbackURL
        unavailableMessage = nil
    }

    func reset(fallbackURL: URL? = nil) {
        requestToken = UUID()
        payload = nil
        isLoading = false
        self.fallbackURL = fallbackURL
        unavailableMessage = nil
        selectedTab = .comments
        collapsedCommentIDs = []
    }

    func inlineSummaryPayload(for content: ContentDetail) -> ContentDiscussion? {
        guard let discussion = cachedPayload(for: content),
              discussion.summary != nil else {
            return nil
        }
        return discussion
    }

    func discussionURL(for content: ContentDetail) -> URL? {
        let rawURL = normalizedText(content.newsDiscussionURL)
            ?? normalizedText(content.newsMetadata?.discussionURL)
        guard let rawURL else { return nil }
        return URL(string: rawURL)
    }

    func load(
        content: ContentDetail,
        fallbackURL: URL,
        refresh: Bool = false,
        currentContentId: Int?
    ) async {
        self.fallbackURL = fallbackURL

        if !refresh, let discussion = cachedPayload(for: content), discussion.hasRenderableContent {
            applyPayload(discussion)
            return
        }

        if isLoading { return }

        let token = UUID()
        requestToken = token
        isLoading = true
        if refresh || cachedPayload(for: content) == nil {
            payload = nil
        }
        unavailableMessage = nil
        defer { isLoading = false }

        do {
            let discussion: ContentDiscussion
            if refresh {
                discussion = try await contentService.refreshContentDiscussion(
                    id: content.id,
                    contentType: content.contentType
                )
            } else {
                discussion = try await contentService.fetchContentDiscussion(
                    id: content.id,
                    contentType: content.contentType
                )
            }

            guard requestToken == token,
                  currentContentId == content.id else {
                return
            }

            applyPayload(discussion)
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            guard requestToken == token else { return }
            unavailableMessage = "Comments could not be loaded right now."
        }
    }

    func prefetchStoredDiscussion(
        for content: ContentDetail,
        currentContentId: Int?
    ) async {
        guard content.contentType == .news,
              discussionURL(for: content) != nil else {
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
                  discussion.hasRenderableContent else {
                return
            }
            applyPayload(discussion, showUnavailable: false)
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            discussionSheetLogger.debug(
                "Stored discussion prefetch failed | contentId=\(content.id) error=\(error.localizedDescription)"
            )
        }
    }

    private func cachedPayload(for content: ContentDetail) -> ContentDiscussion? {
        guard let payload,
              payload.contentId == content.id else {
            return nil
        }
        return payload
    }

    private func applyPayload(
        _ discussion: ContentDiscussion,
        showUnavailable: Bool = true
    ) {
        payload = discussion
        if discussion.hasRenderableContent {
            unavailableMessage = nil
            selectedTab = .comments
            collapsedCommentIDs = []
        } else if showUnavailable {
            unavailableMessage = discussion.unavailableMessage
        }
    }

    private func normalizedText(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
