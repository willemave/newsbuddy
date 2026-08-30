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
    struct ContentKey: Hashable {
        let id: Int
        let contentType: APIContentType?
    }

    enum InitialLoadPhase: Equatable {
        case idle
        case loading
        case failure(String)
    }

    enum RevalidationPhase: Equatable {
        case idle
        case refreshing
        case failure(String)
    }

    private enum TaskKey: Hashable {
        case primary(ContentKey)
        case sourceBody(ContentKey)
        case readerBody(ContentKey)
        case markRead(ContentKey)
        case trackOpened(ContentKey)
    }

    var content: ContentDetail?
    var contentBody: ContentBody?
    var readerBody: ContentBody?
    var isLoadingReaderBody = false
    var readerErrorMessage: String?
    private(set) var initialLoadPhase: InitialLoadPhase = .idle
    private(set) var revalidationPhase: RevalidationPhase = .idle
    // Indicates if the item was already marked as read when it was fetched
    var wasAlreadyReadWhenLoaded: Bool = false

    // Feed subscription state
    var isSubscribingToFeed = false
    var feedSubscriptionSuccessMessage: String?
    var feedSubscriptionError: String?
    private var linkSubmissionRevision = 0

    var feedSubscriptionSuccess: Bool { feedSubscriptionSuccessMessage != nil }
    var isLoading: Bool {
        content == nil && initialLoadPhase == .loading
    }
    var errorMessage: String? {
        guard content == nil,
              case .failure(let message) = initialLoadPhase else { return nil }
        return message
    }

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
    private let tasks = TaskBag<TaskKey>()
    @ObservationIgnored
    private var contentId: Int = 0
    @ObservationIgnored
    private var contentType: APIContentType?

    private var currentContentKey: ContentKey {
        ContentKey(id: contentId, contentType: contentType)
    }
    
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
        tasks.cancelAll()
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
        self.initialLoadPhase = .loading
        self.revalidationPhase = .idle
        self.isSubscribingToFeed = false
        self.feedSubscriptionSuccessMessage = nil
        self.feedSubscriptionError = nil
        linkSubmissionCoordinator.reset()
    }
    
    func loadContent() async {
        let key = currentContentKey
        let taskKey = TaskKey.primary(key)
        if let existingTask = tasks.task(for: taskKey) {
            await existingTask.value
            return
        }

        let isRevalidation = content?.id == key.id
        if isRevalidation {
            revalidationPhase = .refreshing
        } else {
            initialLoadPhase = .loading
        }
        logger.info("[ContentDetail] loadContent started | contentId=\(key.id)")

        let task = tasks.runReplacing(taskKey) { [weak self] token in
            guard let self else { return }
            await self.performPrimaryLoad(
                key: key,
                isRevalidation: isRevalidation,
                token: token
            )
        }
        await task.value
    }

    func revalidateContent() async {
        await loadContent()
    }

    func suspendAutomaticReads() {
        tasks.cancel(.primary(currentContentKey))
        if let content {
            let concreteKey = ContentKey(id: content.id, contentType: content.contentType)
            tasks.cancel(.sourceBody(concreteKey))
            tasks.cancel(.readerBody(concreteKey))
        }
        if content == nil {
            initialLoadPhase = .idle
        }
        revalidationPhase = .idle
        isLoadingReaderBody = false
    }

    private func performPrimaryLoad(
        key: ContentKey,
        isRevalidation: Bool,
        token: TaskBag<TaskKey>.Token
    ) async {
        let requestedContentId = key.id
        let requestedContentType = key.contentType

        do {
            logger.debug("[ContentDetail] Fetching content detail | contentId=\(requestedContentId) contentType=\(requestedContentType?.rawValue ?? "nil", privacy: .public)")
            let fetched: ContentDetail
            if requestedContentType == .news {
                fetched = try await contentService.fetchNewsItemDetail(id: requestedContentId)
            } else {
                fetched = try await contentService.fetchContentDetail(id: requestedContentId)
            }

            guard tasks.isCurrent(token), currentContentKey == key else {
                logger.debug(
                    "[ContentDetail] Ignoring stale content detail | requestedId=\(requestedContentId) currentId=\(self.contentId)"
                )
                return
            }

            let isFirstWinningLoad = content?.id != fetched.id
            content = fetched
            logger.info("[ContentDetail] Content fetched | contentId=\(requestedContentId) type=\(fetched.contentType.rawValue, privacy: .public) isRead=\(fetched.isRead) title=\(fetched.displayTitle, privacy: .public)")

            // Capture read state as returned by the server BEFORE any auto-marking
            if isFirstWinningLoad {
                wasAlreadyReadWhenLoaded = fetched.isRead
            }
            logger.debug("[ContentDetail] wasAlreadyReadWhenLoaded=\(fetched.isRead) | contentId=\(requestedContentId)")

            initialLoadPhase = .idle
            revalidationPhase = .idle

            let fetchedKey = ContentKey(id: fetched.id, contentType: fetched.contentType)
            if isFirstWinningLoad {
                tasks.runReplacing(.trackOpened(fetchedKey)) { [weak self] in
                    guard let self else { return }
                    await self.trackOpenedInteraction(for: fetched)
                }

                tasks.runReplacing(.markRead(fetchedKey)) { [weak self] in
                    guard let self else { return }
                    await self.markFetchedContentAsReadIfNeeded(fetched)
                }
            }

            if fetched.bodyAvailable,
               fetched.contentType != .news,
               contentBody?.contentId != fetched.id {
                tasks.runReplacing(.sourceBody(fetchedKey)) { [weak self] token in
                    guard let self else { return }
                    await self.loadContentBody(for: fetched, token: token)
                }
            }
        } catch where ClientFailure.classify(error) == .cancelled {
            guard tasks.isCurrent(token), currentContentKey == key else { return }
            if isRevalidation {
                revalidationPhase = .idle
            } else {
                initialLoadPhase = .idle
            }
        } catch {
            guard tasks.isCurrent(token), currentContentKey == key else { return }
            logger.error("[ContentDetail] Error loading content | contentId=\(requestedContentId) error=\(error.localizedDescription)")
            if isRevalidation, content != nil {
                revalidationPhase = .failure(error.localizedDescription)
            } else {
                initialLoadPhase = .failure(error.localizedDescription)
            }
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
        let contentKey = ContentKey(id: content.id, contentType: content.contentType)
        let taskKey = TaskKey.readerBody(contentKey)
        if !force, let existingTask = tasks.task(for: taskKey) {
            await existingTask.value
            return
        }

        let task = tasks.runReplacing(taskKey) { [weak self] token in
            guard let self else { return }
            self.isLoadingReaderBody = true
            self.readerErrorMessage = nil
            defer {
                if self.tasks.isCurrent(token) {
                    self.isLoadingReaderBody = false
                }
            }

            await self.performReaderBodyLoad(for: content, token: token)
        }
        await task.value
    }

    private func performReaderBodyLoad(
        for content: ContentDetail,
        token: TaskBag<TaskKey>.Token
    ) async {
        do {
            let body = try await fetchReaderBody(for: content)
            guard tasks.isCurrent(token),
                  self.contentId == content.id,
                  self.content?.id == content.id,
                  self.content?.contentType == content.contentType else {
                logger.debug("[ContentDetail] Ignoring stale reader body | requestedId=\(content.id) currentId=\(self.contentId)")
                return
            }
            readerBody = body
        } catch where ClientFailure.classify(error) == .cancelled {
            return
        } catch {
            guard tasks.isCurrent(token), self.contentId == content.id else { return }
            logger.error("[ContentDetail] Failed to fetch reader body | contentId=\(content.id) error=\(error.localizedDescription)")
            readerErrorMessage = error.localizedDescription
        }
    }

    private func loadContentBody(
        for fetched: ContentDetail,
        token: TaskBag<TaskKey>.Token
    ) async {
        do {
            let body = try await contentService.fetchContentBody(
                id: fetched.id,
                variant: "source",
                contentType: fetched.contentType
            )
            guard tasks.isCurrent(token),
                  self.contentId == fetched.id,
                  self.content?.contentType == fetched.contentType else {
                logger.debug("[ContentDetail] Ignoring stale content body | requestedId=\(fetched.id) currentId=\(self.contentId)")
                return
            }
            contentBody = body
        } catch where ClientFailure.classify(error) == .cancelled {
            return
        } catch {
            guard tasks.isCurrent(token) else { return }
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
        } catch where ClientFailure.classify(error) == .cancelled {
            throw error
        } catch {
            logger.debug("[ContentDetail] Rendered reader body unavailable, falling back to source | contentId=\(content.id) error=\(error.localizedDescription)")
        }

        if let sourceBody = contentBody,
           sourceBody.contentId == content.id {
            return sourceBody
        }

        let contentKey = ContentKey(id: content.id, contentType: content.contentType)
        if let sourceTask = tasks.task(for: .sourceBody(contentKey)) {
            await sourceTask.value
            if let sourceBody = contentBody,
               sourceBody.contentId == content.id {
                return sourceBody
            }
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
        } catch where ClientFailure.classify(error) == .cancelled {
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
        } catch where ClientFailure.classify(error) == .cancelled {
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
            toastPresenter.showError("Failed to update knowledge save")
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

    func relevantLinkReadLaterState(for linkID: String) -> LinkReadLaterState {
        _ = linkSubmissionRevision
        return linkSubmissionCoordinator.state(for: linkID)
    }

    func addRelevantLinkToReadLater(_ link: RelevantLink) async {
        await linkSubmissionCoordinator.addRelevantLinkToReadLater(link)
    }

    /// Subscribe to the detected feed for this content.
    func subscribeToDetectedFeed() async {
        guard let feed = content?.detectedFeed else {
            feedSubscriptionError = "No feed detected"
            return
        }
        let requestedContentId = contentId
        let requestedFeedURL = feed.url

        isSubscribingToFeed = true
        feedSubscriptionError = nil
        defer {
            if contentId == requestedContentId,
               content?.detectedFeed?.url == requestedFeedURL {
                isSubscribingToFeed = false
            }
        }

        do {
            let config = try await feedSubscriptionService.subscribeFeed(
                feedURL: feed.url,
                feedType: feed.type,
                displayName: feed.title
            )
            guard contentId == requestedContentId,
                  content?.detectedFeed?.url == requestedFeedURL else { return }
            feedSubscriptionSuccessMessage = config.subscriptionOutcome == .already_subscribed
                ? "This source was already in your feed"
                : "You'll now receive new content from this source"
            logger.info("[ContentDetail] Successfully subscribed to feed | url=\(feed.url, privacy: .public) type=\(feed.type, privacy: .public)")
        } catch {
            guard contentId == requestedContentId,
                  content?.detectedFeed?.url == requestedFeedURL else { return }
            feedSubscriptionError = "Couldn't subscribe. Please try again."
            logger.error("[ContentDetail] Failed to subscribe to feed | error=\(error.localizedDescription)")
        }
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

}
