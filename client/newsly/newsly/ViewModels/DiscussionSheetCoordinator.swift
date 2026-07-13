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

enum DiscussionCommentsDestination: Equatable {
    case inlineSummary
    case sheet
}

enum DiscussionCommentsNavigationAction: Equatable {
    case none
    case waitForPayload
    case scrollInlineSummary
    case presentSheet
}

struct DiscussionCommentsNavigationState: Equatable {
    private let requestID: UUID
    var contentId: Int?
    var fallbackURL: URL?

    init(
        contentId: Int? = nil,
        fallbackURL: URL? = nil,
        requestID: UUID = UUID()
    ) {
        self.requestID = requestID
        self.contentId = contentId
        self.fallbackURL = fallbackURL
    }

    func refreshed() -> DiscussionCommentsNavigationState {
        DiscussionCommentsNavigationState(
            contentId: contentId,
            fallbackURL: fallbackURL
        )
    }
}

@MainActor
@Observable
final class DiscussionSheetCoordinator {
    var payload: ContentDiscussion?
    var isLoading = false
    var fallbackURL: URL?
    var unavailableMessage: String?
    var selectedTab: DiscussionTab = .comments
    var collapsedCommentIDs: Set<String> = []
    var commentsNavigationState = DiscussionCommentsNavigationState()

    private var requestToken = UUID()
    private var pendingCommentsNavigation: DiscussionCommentsNavigationState?
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

    func requestCommentsNavigation(content: ContentDetail, fallbackURL: URL) {
        let state = DiscussionCommentsNavigationState(
            contentId: content.id,
            fallbackURL: fallbackURL
        )
        pendingCommentsNavigation = state
        commentsNavigationState = state
        prepareForPresentation(fallbackURL: fallbackURL)
    }

    func cancelCommentsNavigation() {
        pendingCommentsNavigation = nil
        commentsNavigationState = DiscussionCommentsNavigationState()
    }

    func resolveCommentsDestination(
        content: ContentDetail,
        fallbackURL: URL,
        currentContentId: Int?
    ) async -> DiscussionCommentsDestination {
        prepareForPresentation(fallbackURL: fallbackURL)

        if let discussion = cachedPayload(for: content), discussion.hasRenderableContent {
            applyPayload(discussion)
            return discussion.summary == nil ? .sheet : .inlineSummary
        }

        await load(
            content: content,
            fallbackURL: fallbackURL,
            currentContentId: currentContentId
        )

        while isLoading {
            guard !Task.isCancelled else { return .sheet }
            try? await Task.sleep(nanoseconds: 25_000_000)
        }

        guard !Task.isCancelled,
              let discussion = cachedPayload(for: content),
              discussion.hasRenderableContent else {
            return .sheet
        }
        return discussion.summary == nil ? .sheet : .inlineSummary
    }

    func loadPendingCommentsNavigation(
        content: ContentDetail,
        currentContentId: Int?
    ) async {
        guard let pendingCommentsNavigation,
              pendingCommentsNavigation.contentId == content.id,
              let fallbackURL = pendingCommentsNavigation.fallbackURL else {
            return
        }

        if let discussion = cachedPayload(for: content), discussion.hasRenderableContent {
            applyPayload(discussion)
            commentsNavigationState = pendingCommentsNavigation.refreshed()
            return
        }

        await load(
            content: content,
            fallbackURL: fallbackURL,
            currentContentId: currentContentId
        )
        commentsNavigationState = pendingCommentsNavigation.refreshed()
    }

    func commentsNavigationAction(for content: ContentDetail?) -> DiscussionCommentsNavigationAction {
        guard let pendingCommentsNavigation else {
            return .none
        }
        guard let content,
              pendingCommentsNavigation.contentId == content.id else {
            return .none
        }

        if isLoading {
            return .waitForPayload
        }

        guard let discussion = cachedPayload(for: content) else {
            if unavailableMessage == nil {
                return .waitForPayload
            }
            self.pendingCommentsNavigation = nil
            return .presentSheet
        }

        guard discussion.hasRenderableContent else {
            self.pendingCommentsNavigation = nil
            return .presentSheet
        }

        self.pendingCommentsNavigation = nil
        return discussion.summary == nil ? .presentSheet : .scrollInlineSummary
    }

    func reset(fallbackURL: URL? = nil) {
        requestToken = UUID()
        payload = nil
        isLoading = false
        self.fallbackURL = fallbackURL
        unavailableMessage = nil
        selectedTab = .comments
        collapsedCommentIDs = []
        cancelCommentsNavigation()
    }

    func inlineSummaryPayload(for content: ContentDetail) -> ContentDiscussion? {
        guard let discussion = cachedPayload(for: content),
              discussion.summary != nil || !discussion.comments.isEmpty else {
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
