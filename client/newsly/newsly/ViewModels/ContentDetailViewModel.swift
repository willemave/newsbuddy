//
//  ContentDetailViewModel.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation
import Observation
import SwiftUI
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ContentDetail")

protocol ContentDetailServicing: AnyObject {
    func submitContent(url: URL, contentType: String?, title: String?, platform: String?) async throws -> SubmitContentResponse
    func fetchContentDetail(id: Int) async throws -> ContentDetail
    func fetchNewsItemDetail(id: Int) async throws -> ContentDetail
    func fetchContentBody(id: Int, variant: String, contentType: APIContentType?) async throws -> ContentBody
    func trackContentOpened(contentId: Int, surface: String, contextData: [String: Any]) async throws -> TrackContentInteractionResponse
    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse
    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse
    func convertNewsToArticle(id: Int) async throws -> ConvertNewsResponse
    func convertNewsItemToArticle(id: Int) async throws -> ConvertNewsResponse
    func downloadMoreFromSeries(contentId: Int, count: Int) async throws -> DownloadMoreResponse
}

protocol DetectedFeedSubscribing: AnyObject {
    func subscribeFeed(feedURL: String, feedType: String, displayName: String?) async throws -> ScraperConfig
}

extension ContentService: ContentDetailServicing {}
extension ScraperConfigService: DetectedFeedSubscribing {}

@MainActor
@Observable
final class ContentDetailViewModel {
    var content: ContentDetail?
    var contentBody: ContentBody?
    var readerBody: ContentBody?
    var isLoadingReaderBody = false
    var readerErrorMessage: String?
    var isLoading = false
    var errorMessage: String?
    // Indicates if the item was already marked as read when it was fetched
    var wasAlreadyReadWhenLoaded: Bool = false

    // Feed subscription state
    var isSubscribingToFeed = false
    var feedSubscriptionSuccess = false
    var feedSubscriptionError: String?
    private var linkSubmissionRevision = 0

    @ObservationIgnored
    private let contentService: any ContentDetailServicing
    @ObservationIgnored
    private let readStateCache: ReadStateCache
    @ObservationIgnored
    private let feedSubscriptionService: any DetectedFeedSubscribing
    @ObservationIgnored
    private let toastPresenter: any ToastPresenting
    @ObservationIgnored
    private let linkSubmissionCoordinator: LinkSubmissionCoordinator
    @ObservationIgnored
    private var contentId: Int = 0
    @ObservationIgnored
    private var contentType: APIContentType?
    
    init(
        contentId: Int = 0,
        contentType: APIContentType? = nil,
        contentService: any ContentDetailServicing,
        feedSubscriptionService: any DetectedFeedSubscribing,
        toastPresenter: any ToastPresenting,
        readStateCache: ReadStateCache? = nil,
        submitLinkToLongFormHandler: LinkSubmissionCoordinator.SubmitHandler? = nil
    ) {
        self.contentId = contentId
        self.contentType = contentType
        self.contentService = contentService
        self.feedSubscriptionService = feedSubscriptionService
        self.toastPresenter = toastPresenter
        self.readStateCache = readStateCache ?? ReadStateCache()
        let resolvedSubmitHandler = submitLinkToLongFormHandler ?? { [contentService] url, title in
            try await contentService.submitContent(
                url: url,
                contentType: nil,
                title: title,
                platform: nil
            )
        }
        let linkSubmissionCoordinator = LinkSubmissionCoordinator(
            submitLinkToLongFormHandler: resolvedSubmitHandler,
            toastPresenter: toastPresenter
        )
        self.linkSubmissionCoordinator = linkSubmissionCoordinator
        linkSubmissionCoordinator.onStateWillChange = { [weak self] in
            self?.linkSubmissionRevision += 1
        }
    }
    
    func updateContentId(_ newId: Int, contentType newContentType: APIContentType? = nil) {
        self.contentId = newId
        if let newContentType {
            self.contentType = newContentType
        }
        // Clear previous content to show loading state
        self.content = nil
        self.contentBody = nil
        self.readerBody = nil
        self.isLoadingReaderBody = false
        self.readerErrorMessage = nil
        self.errorMessage = nil
        self.isLoading = true
        linkSubmissionCoordinator.reset()
    }
    
    func loadContent() async {
        let requestedContentId = contentId
        let requestedContentType = contentType
        logger.info("[ContentDetail] loadContent started | contentId=\(requestedContentId)")
        isLoading = true
        errorMessage = nil
        contentBody = nil

        do {
            logger.debug("[ContentDetail] Fetching content detail | contentId=\(requestedContentId) contentType=\(requestedContentType?.rawValue ?? "nil", privacy: .public)")
            let fetched: ContentDetail
            if requestedContentType == .news {
                fetched = try await contentService.fetchNewsItemDetail(id: requestedContentId)
            } else {
                fetched = try await contentService.fetchContentDetail(id: requestedContentId)
            }

            guard contentId == requestedContentId,
                  contentType == requestedContentType else {
                logger.debug(
                    "[ContentDetail] Ignoring stale content detail | requestedId=\(requestedContentId) currentId=\(self.contentId)"
                )
                return
            }

            content = fetched
            logger.info("[ContentDetail] Content fetched | contentId=\(requestedContentId) type=\(fetched.contentType.rawValue, privacy: .public) isRead=\(fetched.isRead) title=\(fetched.displayTitle, privacy: .public)")

            // Capture read state as returned by the server BEFORE any auto-marking
            wasAlreadyReadWhenLoaded = fetched.isRead
            logger.debug("[ContentDetail] wasAlreadyReadWhenLoaded=\(fetched.isRead) | contentId=\(requestedContentId)")

            // Render immediately once the main detail payload arrives.
            isLoading = false

            Task {
                await self.trackOpenedInteraction(for: fetched)
            }

            if fetched.bodyAvailable && fetched.contentType != .news {
                Task {
                    await self.loadContentBody(for: fetched)
                }
            }

            Task {
                await self.markFetchedContentAsReadIfNeeded(fetched)
            }
        } catch where isNetworkCancellation(error) {
            guard contentId == requestedContentId,
                  contentType == requestedContentType else { return }
            isLoading = false
        } catch {
            guard contentId == requestedContentId,
                  contentType == requestedContentType else { return }
            logger.error("[ContentDetail] Error loading content | contentId=\(requestedContentId) error=\(error.localizedDescription)")
            errorMessage = error.localizedDescription
            isLoading = false
        }
        logger.debug("[ContentDetail] loadContent completed | contentId=\(requestedContentId)")
    }

    func canShowReader(for content: ContentDetail) -> Bool {
        guard content.bodyAvailable else { return false }
        return content.contentType == .article || content.contentType == .news
    }

    func loadReaderBody(for content: ContentDetail, force: Bool = false) async {
        guard canShowReader(for: content) else { return }
        guard force || readerBody == nil else { return }
        guard !isLoadingReaderBody else { return }

        isLoadingReaderBody = true
        readerErrorMessage = nil
        defer { isLoadingReaderBody = false }

        do {
            let body = try await fetchReaderBody(for: content)
            guard self.contentId == content.id,
                  self.content?.id == content.id,
                  self.content?.contentType == content.contentType else {
                logger.debug("[ContentDetail] Ignoring stale reader body | requestedId=\(content.id) currentId=\(self.contentId)")
                return
            }
            readerBody = body
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            guard self.contentId == content.id else { return }
            logger.error("[ContentDetail] Failed to fetch reader body | contentId=\(content.id) error=\(error.localizedDescription)")
            readerErrorMessage = error.localizedDescription
        }
    }

    private func loadContentBody(for fetched: ContentDetail) async {
        do {
            let body = try await contentService.fetchContentBody(
                id: fetched.id,
                variant: "source",
                contentType: fetched.contentType
            )
            guard self.contentId == fetched.id else {
                logger.debug("[ContentDetail] Ignoring stale content body | requestedId=\(fetched.id) currentId=\(self.contentId)")
                return
            }
            contentBody = body
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            logger.error("[ContentDetail] Failed to fetch content body | contentId=\(fetched.id) error=\(error.localizedDescription)")
        }
    }

    private func fetchReaderBody(for content: ContentDetail) async throws -> ContentBody {
        do {
            let renderedBody = try await contentService.fetchContentBody(
                id: content.id,
                variant: "rendered",
                contentType: content.contentType
            )
            if !renderedBody.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return renderedBody
            }
        } catch where isNetworkCancellation(error) {
            throw error
        } catch {
            logger.debug("[ContentDetail] Rendered reader body unavailable, falling back to source | contentId=\(content.id) error=\(error.localizedDescription)")
        }

        return try await contentService.fetchContentBody(
            id: content.id,
            variant: "source",
            contentType: content.contentType
        )
    }

    private func markFetchedContentAsReadIfNeeded(_ fetched: ContentDetail) async {
        guard !fetched.isRead else {
            logger.info("[ContentDetail] Content already read, skipping mark-as-read | contentId=\(fetched.id)")
            return
        }

        // Only issue the server-side mark-as-read for the item that is still
        // current. During fast cascade navigation this task can run after the
        // user has already moved on, and the network call fires before the
        // post-await guard below — without this check, transited (unread) items
        // get marked read server-side and their counts decremented.
        guard self.contentId == fetched.id else {
            logger.debug("[ContentDetail] Skipping mark-as-read for transited item | requestedId=\(fetched.id) currentId=\(self.contentId)")
            return
        }

        do {
            logger.info("[ContentDetail] Content not read, marking as read | contentId=\(fetched.id) type=\(fetched.contentType.rawValue, privacy: .public)")
            try await readStateCache.markReadAndSync([
                ReadStateKey(id: fetched.id, contentType: fetched.contentType)
            ])
            logger.info("[ContentDetail] Successfully marked as read | contentId=\(fetched.id)")

            guard self.contentId == fetched.id else {
                logger.debug("[ContentDetail] Ignoring stale mark-as-read completion | requestedId=\(fetched.id) currentId=\(self.contentId)")
                return
            }

            content?.isRead = true
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            logger.error("[ContentDetail] Failed to mark content as read | contentId=\(fetched.id) error=\(error.localizedDescription)")
        }
    }

    private func trackOpenedInteraction(for fetched: ContentDetail) async {
        let contextData: [String: Any] = [
            "content_type": fetched.contentType.rawValue,
            "was_read_when_loaded": fetched.isRead,
        ]

        do {
            let response = try await contentService.trackContentOpened(
                contentId: fetched.id,
                surface: "ios_content_detail",
                contextData: contextData
            )
            logger.debug(
                "[ContentDetail] Open interaction tracked | contentId=\(fetched.id) recorded=\(response.recorded)"
            )
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            logger.error(
                "[ContentDetail] Failed to track open interaction | contentId=\(fetched.id) error=\(error.localizedDescription)"
            )
        }
    }
    
    func shareContent(option: ShareContentOption) {
        let items = buildShareItems(option: option)
        guard !items.isEmpty else { return }

        let activityVC = UIActivityViewController(activityItems: items, applicationActivities: nil)
        ActivityViewPresenter.presentWhenReady(activityVC)
    }

    func markdownForShare(option: ShareContentOption) -> String? {
        guard let content else { return nil }
        return ShareMarkdownBuilder(content: content, contentBody: contentBody)
            .markdown(for: option)
    }
    
    func toggleKnowledgeSave() async {
        guard let currentContent = content else { return }

        do {
            let targetSavedState = !currentContent.isSavedToKnowledge
            content?.isSavedToKnowledge = targetSavedState
            if targetSavedState {
                let response = try await contentService.saveToKnowledge(id: currentContent.id)
                content?.isSavedToKnowledge = response.isSavedToKnowledge
            } else {
                let response = try await contentService.removeFromKnowledge(id: currentContent.id)
                content?.isSavedToKnowledge = response.isSavedToKnowledge
            }
        } catch {
            content?.isSavedToKnowledge = currentContent.isSavedToKnowledge
            errorMessage = "Failed to update knowledge save"
        }
    }

    func saveLinkedArticleAsKnowledge() async {
        guard let currentContent = content, currentContent.contentType == .news else {
            return
        }

        do {
            let response: ConvertNewsResponse
            if contentType == .news {
                response = try await contentService.convertNewsItemToArticle(id: currentContent.id)
            } else {
                response = try await contentService.convertNewsToArticle(id: currentContent.id)
            }

            if response.alreadyExists {
                toastPresenter.show("Article already saved to Knowledge", type: .info, duration: 3.0)
            } else {
                toastPresenter.showSuccess("Saved linked article to Knowledge")
            }
        } catch {
            toastPresenter.showError("Failed to save linked article: \(error.localizedDescription)")
        }
    }

    func discussionLinkAddState(for linkID: String) -> DiscussionLinkAddState {
        _ = linkSubmissionRevision
        return linkSubmissionCoordinator.state(for: linkID)
    }

    func relevantLinkReadLaterState(for linkID: String) -> LinkReadLaterState {
        _ = linkSubmissionRevision
        return linkSubmissionCoordinator.state(for: linkID)
    }

    func addRelevantLinkToReadLater(_ link: RelevantLink) async {
        await linkSubmissionCoordinator.addRelevantLinkToReadLater(link)
    }

    func addDiscussionLinkToLongForm(_ link: DiscussionLink) async {
        await linkSubmissionCoordinator.addDiscussionLinkToLongForm(link)
    }

    /// Subscribe to the detected feed for this content.
    func subscribeToDetectedFeed() async {
        guard let feed = content?.detectedFeed else {
            feedSubscriptionError = "No feed detected"
            return
        }

        isSubscribingToFeed = true
        feedSubscriptionError = nil

        do {
            _ = try await feedSubscriptionService.subscribeFeed(
                feedURL: feed.url,
                feedType: feed.type,
                displayName: feed.title
            )
            feedSubscriptionSuccess = true
            logger.info("[ContentDetail] Successfully subscribed to feed | url=\(feed.url, privacy: .public) type=\(feed.type, privacy: .public)")
        } catch {
            feedSubscriptionError = error.localizedDescription
            logger.error("[ContentDetail] Failed to subscribe to feed | error=\(error.localizedDescription)")
        }

        isSubscribingToFeed = false
    }

    func downloadMoreFromSeries(count: Int) async {
        guard let contentId = content?.id else { return }

        do {
            let response = try await contentService.downloadMoreFromSeries(
                contentId: contentId,
                count: count
            )
            let savedCount = response.saved
            if savedCount > 0 {
                toastPresenter.showSuccess("Added \(savedCount) new items")
            } else {
                toastPresenter.show("Download started", type: .info, duration: 3.0)
            }
        } catch {
            toastPresenter.showError("Failed to download more: \(error.localizedDescription)")
        }
    }

    private func buildShareItems(option: ShareContentOption) -> [Any] {
        guard let content = content else { return [] }
        let builder = ShareMarkdownBuilder(content: content, contentBody: contentBody)

        switch option {
        case .light:
            var items: [Any] = [content.displayTitle]
            if let shareURL = builder.shareURLString {
                if let url = URL(string: shareURL) {
                    items.append(url)
                } else {
                    items.append(shareURL)
                }
            }
            return items
        case .medium:
            if let mediumText = builder.markdown(for: .medium) {
                return [MarkdownItemProvider(markdown: mediumText, subject: content.displayTitle)]
            }
            return buildShareItems(option: .light)
        case .full:
            if let fullText = builder.markdown(for: .full) {
                return [MarkdownItemProvider(markdown: fullText, subject: content.displayTitle)]
            }
            return buildShareItems(option: .medium)
        }
    }

    func openInChatGPT() async {
        // Strategy:
        // 1) Build full markdown and offer it via the share sheet so ChatGPT's share extension can receive the text.
        // 2) As a convenience, also put the text on the clipboard (user can paste if needed in the app).
        // 3) Use custom item provider to preserve line breaks in Mail by converting to HTML.

        guard let content = content else { return }
        let fullText = ShareMarkdownBuilder(content: content, contentBody: contentBody)
            .markdown(for: .full) ?? content.displayTitle

        // Put on clipboard (helps in case target app reads clipboard or the user wants to paste manually)
        UIPasteboard.general.string = fullText

        // Create custom item provider that converts markdown to HTML for Mail
        let itemProvider = MarkdownItemProvider(markdown: fullText, subject: content.displayTitle)

        // Prepare share sheet with custom provider
        let activityVC = UIActivityViewController(activityItems: [itemProvider], applicationActivities: nil)
        activityVC.excludedActivityTypes = [.assignToContact, .saveToCameraRoll, .addToReadingList, .postToFacebook, .postToTwitter]
        ActivityViewPresenter.presentWhenReady(activityVC)
    }
}
