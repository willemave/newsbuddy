//
//  LongContentListViewModel.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Combine
import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "LongContentList")

@MainActor
final class LongContentListViewModel: BaseContentListViewModel {
    private let readRepository: ReadStatusRepositoryType
    private let unreadCountService: UnreadCountService
    private let contentService: ContentService

    private var cancellables = Set<AnyCancellable>()

    init(
        repository: ContentRepositoryType,
        readRepository: ReadStatusRepositoryType,
        unreadCountService: UnreadCountService,
        contentService: ContentService = .shared
    ) {
        self.readRepository = readRepository
        self.unreadCountService = unreadCountService
        self.contentService = contentService
        super.init(
            repository: repository,
            contentTypes: [.article, .podcast],
            readFilter: .unread
        )
        bindReadStatusNotifications()
        logger.info("[LongContentList] ViewModel initialized")
    }

    private func bindReadStatusNotifications() {
        NotificationCenter.default.publisher(for: .contentMarkedAsRead)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] notification in
                guard let self,
                      let userInfo = notification.userInfo,
                      let contentId = userInfo["contentId"] as? Int,
                      let contentType = userInfo["contentType"] as? String
                else {
                    logger.warning("[LongContentList] Received contentMarkedAsRead with invalid userInfo")
                    return
                }

                logger.info("[LongContentList] Received contentMarkedAsRead notification | contentId=\(contentId) type=\(contentType, privacy: .public)")

                // Only update if it's article or podcast content
                guard let apiType = APIContentType(rawValue: contentType),
                      apiType == .article || apiType == .podcast
                else {
                    logger.debug("[LongContentList] Ignoring non-article/podcast content | contentId=\(contentId) type=\(contentType, privacy: .public)")
                    return
                }

                logger.info("[LongContentList] Updating local read state | contentId=\(contentId)")
                let shouldDropReadItems = currentReadFilter() == .unread
                let markedItems = markItemsLocallyRead(
                    ids: [contentId],
                    removeReadItems: shouldDropReadItems
                )
                if markedItems.isEmpty && shouldDropReadItems {
                    dropReadItems()
                }
            }
            .store(in: &cancellables)
    }

    func refresh() {
        logger.info("[LongContentList] refresh called")
        startInitialLoad()
    }

    func ensureUnreadFeedLoaded() {
        let previousFilter = currentReadFilter()
        setReadFilter(.unread)

        guard previousFilter == .unread else { return }
        guard currentItems().isEmpty else { return }
        refresh()
    }

    func refreshUnreadFeed() {
        let previousFilter = currentReadFilter()
        setReadFilter(.unread)

        guard previousFilter == .unread else { return }
        refresh()
    }

    func refreshUnreadFeedInBackground() {
        let previousFilter = currentReadFilter()
        setReadFilter(.unread)

        guard previousFilter == .unread else { return }
        refreshInBackground()
    }

    func setReadFilter(_ filter: ReadFilter) {
        logger.info("[LongContentList] setReadFilter | filter=\(String(describing: filter), privacy: .public)")
        updateReadFilter(filter)
    }

    func markAsRead(_ id: Int) {
        logger.info("[LongContentList] markAsRead called | id=\(id)")

        guard let item = currentItems().first(where: { $0.id == id }) else {
            logger.warning("[LongContentList] markAsRead failed: item not found | id=\(id)")
            return
        }
        guard !item.isRead else {
            logger.debug("[LongContentList] markAsRead skipped: item already read | id=\(id)")
            return
        }

        let markedItems = markItemsLocallyRead(
            ids: [id],
            removeReadItems: currentReadFilter() == .unread
        )
        guard let markedItem = markedItems.first else { return }

        decrementCount(for: markedItem)
        logger.debug("[LongContentList] Marked locally read | id=\(id) type=\(markedItem.contentType, privacy: .public)")

        readRepository
            .markRead(ids: [id])
            .receive(on: DispatchQueue.main)
            .sink { completion in
                if case .failure(let error) = completion {
                    logger.error("[LongContentList] markAsRead API failed | id=\(id) error=\(error.localizedDescription)")
                }
            } receiveValue: { _ in
                logger.info("[LongContentList] markAsRead API success | id=\(id)")
            }
            .store(in: &cancellables)
    }

    func markAllVisibleAsRead() async {
        let unreadItems = currentItems().filter { !$0.isRead }
        guard !unreadItems.isEmpty else {
            logger.debug("[LongContentList] markAllVisibleAsRead: no unread items")
            return
        }

        let ids = unreadItems.map(\.id)
        logger.info("[LongContentList] markAllVisibleAsRead | ids=\(ids, privacy: .public) count=\(ids.count)")

        let markedItems = markItemsLocallyRead(
            ids: ids,
            removeReadItems: currentReadFilter() == .unread
        )
        let markedIds = markedItems.map(\.id)
        guard !markedIds.isEmpty else {
            logger.debug("[LongContentList] markAllVisibleAsRead: all items already read")
            return
        }

        let reductions = markedItems.reduce(into: (articles: 0, podcasts: 0)) { partial, item in
            switch item.contentTypeEnum {
            case .article:
                partial.articles += 1
            case .podcast:
                partial.podcasts += 1
            default:
                break
            }
        }

        if reductions.articles > 0 {
            unreadCountService.decrementArticleCount(by: reductions.articles)
        }
        if reductions.podcasts > 0 {
            unreadCountService.decrementPodcastCount(by: reductions.podcasts)
        }
        logger.debug("[LongContentList] Decremented counts | articles=\(reductions.articles) podcasts=\(reductions.podcasts)")

        await withCheckedContinuation { continuation in
            readRepository
                .markRead(ids: markedIds)
                .receive(on: DispatchQueue.main)
                .sink { completion in
                    if case .failure(let error) = completion {
                        logger.error("[LongContentList] markAllVisibleAsRead API failed | error=\(error.localizedDescription)")
                    }
                    continuation.resume()
                } receiveValue: { _ in
                    logger.info("[LongContentList] markAllVisibleAsRead API success | count=\(markedIds.count)")
                }
                .store(in: &cancellables)
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
                if let isSavedToKnowledge = response["is_saved_to_knowledge"] as? Bool {
                    updateItem(id: contentId) { $0.updating(isSavedToKnowledge: isSavedToKnowledge) }
                    logger.info("[LongContentList] toggleKnowledgeSave success | contentId=\(contentId) isSavedToKnowledge=\(isSavedToKnowledge)")
                }
            } else {
                try await contentService.removeFromKnowledge(id: contentId)
                updateItem(id: contentId) { $0.updating(isSavedToKnowledge: false) }
                logger.info("[LongContentList] toggleKnowledgeSave success | contentId=\(contentId) isSavedToKnowledge=false")
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
                ToastService.shared.showSuccess("Added \(savedCount) new items")
            } else {
                ToastService.shared.show("Download started", type: .info)
            }
        } catch {
            logger.error("[LongContentList] downloadMoreFromSeries failed | contentId=\(contentId) error=\(error.localizedDescription)")
            ToastService.shared.showError("Failed to download more: \(error.localizedDescription)")
        }
    }

    private func decrementCount(for item: ContentSummary) {
        switch item.contentTypeEnum {
        case .article:
            unreadCountService.decrementArticleCount()
        case .podcast:
            unreadCountService.decrementPodcastCount()
        default:
            break
        }
    }
}
