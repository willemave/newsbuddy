//
//  LongContentListViewModel.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Foundation
import Observation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "LongContentList")

@MainActor
@Observable
final class LongContentListViewModel: ContentSummaryFeedEditing {
    var contents: [ContentSummary] {
        get {
            readStateCache.applying(
                to: feed.items,
                removeReadItems: readFilter == .unread
            )
        }
        set { feed.replaceItems(newValue) }
    }

    var state: LoadPhase {
        feed.phase
    }

    var nextCursor: String? {
        feed.nextCursor
    }

    var hasMore: Bool {
        feed.hasMore
    }

    @ObservationIgnored
    private let repository: ContentRepositoryType

    @ObservationIgnored
    private let readStateCache: ReadStateCache

    @ObservationIgnored
    private let contentService: any ContentSummaryListServicing

    @ObservationIgnored
    private let toastPresenter: any ToastPresenting

    @ObservationIgnored
    private var feed: PaginatedFeed<ContentSummary>!

    @ObservationIgnored
    private let loadTasks = FeedLoadTaskRunner()

    private var readFilter: ReadFilter = .unread

    init(
        repository: ContentRepositoryType,
        readRepository: ReadStatusRepositoryType,
        unreadCountService: UnreadCountService,
        contentService: any ContentSummaryListServicing,
        toastPresenter: any ToastPresenting,
        readStateCache: ReadStateCache? = nil
    ) {
        self.repository = repository
        self.readStateCache = readStateCache ?? ReadStateCache(
            contentReadRepository: readRepository,
            unreadCountService: unreadCountService
        )
        self.contentService = contentService
        self.toastPresenter = toastPresenter
        self.feed = PaginatedFeed(
            loadPage: { [weak self] cursor in
                guard let self else {
                    return Page(items: [], nextCursor: nil, hasMore: false)
                }
                return try await self.loadContentPage(cursor: cursor)
            },
            mergeReplacement: PaginatedFeed.mergeNewItemsOnTopKeepingExistingOrder
        )
        logger.info("[LongContentList] ViewModel initialized")
    }

    func refresh() async {
        logger.info("[LongContentList] refresh called")
        await loadTasks.runReplacing { [weak self] in
            guard let self else { return }
            await feed.loadInitial()
        }
    }

    func refreshInBackgroundAndWait() async {
        await loadTasks.runIfIdle { [weak self] in
            guard let self else { return }
            await feed.refreshInBackground()
        }
    }

    func loadNextPage() async {
        await loadTasks.runIfIdle { [weak self] in
            guard let self else { return }
            if currentItems().isEmpty {
                await feed.loadInitial()
            } else {
                await feed.loadNextPage()
            }
        }
    }

    func updateReadFilter(_ newValue: ReadFilter) async {
        guard newValue != readFilter else { return }
        readFilter = newValue
        loadTasks.cancel()
        await refresh()
    }

    func currentReadFilter() -> ReadFilter {
        readFilter
    }

    func ensureUnreadFeedLoaded() async {
        let previousFilter = currentReadFilter()
        await setReadFilter(.unread)

        guard previousFilter == .unread else { return }
        guard currentItems().isEmpty else { return }
        await refresh()
    }

    func refreshUnreadFeed() async {
        let previousFilter = currentReadFilter()
        await setReadFilter(.unread)

        guard previousFilter == .unread else { return }
        await refresh()
    }

    func refreshUnreadFeedInBackground() async {
        let previousFilter = currentReadFilter()
        await setReadFilter(.unread)

        guard previousFilter == .unread else { return }
        await refreshInBackgroundAndWait()
    }

    func setReadFilter(_ filter: ReadFilter) async {
        logger.info("[LongContentList] setReadFilter | filter=\(String(describing: filter), privacy: .public)")
        await updateReadFilter(filter)
    }

    func markAsRead(_ id: Int) async {
        logger.info("[LongContentList] markAsRead called | id=\(id)")

        guard let item = currentItems().first(where: { $0.id == id }) else {
            logger.warning("[LongContentList] markAsRead failed: item not found | id=\(id)")
            return
        }
        guard !item.isRead else {
            logger.debug("[LongContentList] markAsRead skipped: item already read | id=\(id)")
            return
        }

        do {
            try await readStateCache.markReadAndSync([ReadStateKey(item)])
            logger.info("[LongContentList] markAsRead API success | id=\(id)")
        } catch {
            logger.error("[LongContentList] markAsRead API failed | id=\(id) error=\(error.localizedDescription)")
        }
    }

    func markAllVisibleAsRead() async {
        let unreadItems = currentItems().filter { !$0.isRead }
        guard !unreadItems.isEmpty else {
            logger.debug("[LongContentList] markAllVisibleAsRead: no unread items")
            return
        }

        let ids = unreadItems.map(\.id)
        logger.info("[LongContentList] markAllVisibleAsRead | ids=\(ids, privacy: .public) count=\(ids.count)")

        let keys = Set(unreadItems.map(ReadStateKey.init))
        guard !keys.isEmpty else {
            logger.debug("[LongContentList] markAllVisibleAsRead: all items already read")
            return
        }

        do {
            let markedKeys = try await readStateCache.markReadAndSync(keys)
            logger.info("[LongContentList] markAllVisibleAsRead API success | count=\(markedKeys.count)")
        } catch {
            logger.error("[LongContentList] markAllVisibleAsRead API failed | error=\(error.localizedDescription)")
        }
    }

    func toggleKnowledgeSave(_ contentId: Int) async {
        logger.info("[LongContentList] toggleKnowledgeSave called | contentId=\(contentId)")

        guard let current = currentItems().first(where: { $0.id == contentId }) else {
            logger.warning("[LongContentList] toggleKnowledgeSave failed: item not found | contentId=\(contentId)")
            return
        }
        let targetSavedState = !current.isSavedToKnowledge
        updateItem(id: contentId) { $0.updating(isSavedToKnowledge: targetSavedState) }

        do {
            if targetSavedState {
                let response = try await contentService.saveToKnowledge(id: contentId)
                updateItem(id: contentId) { $0.updating(isSavedToKnowledge: response.isSavedToKnowledge) }
                logger.info("[LongContentList] toggleKnowledgeSave success | contentId=\(contentId) isSavedToKnowledge=\(response.isSavedToKnowledge)")
            } else {
                let response = try await contentService.removeFromKnowledge(id: contentId)
                updateItem(id: contentId) { $0.updating(isSavedToKnowledge: response.isSavedToKnowledge) }
                logger.info("[LongContentList] toggleKnowledgeSave success | contentId=\(contentId) isSavedToKnowledge=\(response.isSavedToKnowledge)")
            }
        } catch {
            updateItem(id: contentId) { $0.updating(isSavedToKnowledge: current.isSavedToKnowledge) }
            logger.error("[LongContentList] toggleKnowledgeSave failed | contentId=\(contentId) error=\(error.localizedDescription)")
        }
    }

    func downloadMoreFromSeries(contentId: Int, count: Int) async {
        logger.info("[LongContentList] downloadMoreFromSeries | contentId=\(contentId) count=\(count)")
        do {
            let response = try await contentService.downloadMoreFromSeries(contentId: contentId, count: count)
            let savedCount = response.saved
            if savedCount > 0 {
                toastPresenter.showSuccess("Added \(savedCount) new items")
            } else {
                toastPresenter.show("Download started", type: .info, duration: 3.0)
            }
        } catch {
            logger.error("[LongContentList] downloadMoreFromSeries failed | contentId=\(contentId) error=\(error.localizedDescription)")
            toastPresenter.showError("Failed to download more: \(error.localizedDescription)")
        }
    }

    private func loadContentPage(cursor: String?) async throws -> Page<ContentSummary> {
        let requestReadFilter = readFilter

        do {
            let response = try await repository.loadPage(
                contentTypes: [.article, .podcast],
                readFilter: requestReadFilter,
                cursor: cursor,
                limit: nil
            )
            let incomingItems = readStateCache.applying(
                to: response.contents,
                removeReadItems: requestReadFilter == .unread
            )
            return Page(
                items: incomingItems,
                nextCursor: response.nextCursor,
                hasMore: response.hasMore
            )
        } catch where isNetworkCancellation(error) {
            throw CancellationError()
        }
    }

}
