//
//  ShortNewsListViewModel.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Combine
import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ShortNewsList")

@MainActor
final class ShortNewsListViewModel: BaseContentListViewModel {
    private let readRepository: ReadStatusRepositoryType
    private let unreadCountService: UnreadCountService

    private let itemsToMarkRead = PassthroughSubject<[Int], Never>()
    private var readCancellables = Set<AnyCancellable>()

    init(
        repository: ContentRepositoryType,
        readRepository: ReadStatusRepositoryType,
        unreadCountService: UnreadCountService
    ) {
        self.readRepository = readRepository
        self.unreadCountService = unreadCountService
        super.init(
            repository: repository,
            contentTypes: [.news],
            readFilter: .unread
        )
        bindReadTracking()
        bindReadStatusNotifications()
        logger.info("[ShortNewsList] ViewModel initialized")
    }

    /// Called when items have scrolled past the top of the screen
    func itemsScrolledPastTop(ids: [Int]) {
        guard !ids.isEmpty else { return }
        logger.info("[ShortNewsList] itemsScrolledPastTop | ids=\(ids, privacy: .public) count=\(ids.count)")
        itemsToMarkRead.send(ids)
    }

    func markAllVisibleAsRead() {
        let unreadIds = currentItems().filter { !$0.isRead }.map(\.id)
        guard !unreadIds.isEmpty else {
            logger.debug("[ShortNewsList] markAllVisibleAsRead: no unread items")
            return
        }

        logger.info("[ShortNewsList] markAllVisibleAsRead | ids=\(unreadIds, privacy: .public) count=\(unreadIds.count)")
        markBatchRead(ids: unreadIds)
    }

    // MARK: - Private

    private func bindReadTracking() {
        itemsToMarkRead
            .collect(.byTime(DispatchQueue.main, .milliseconds(300)))
            .map { batches in batches.flatMap { $0 } }
            .filter { !$0.isEmpty }
            .sink { [weak self] ids in
                guard let self else { return }
                // Deduplicate
                let uniqueIds = Array(Set(ids))
                logger.info("[ShortNewsList] Processing scroll-based mark read | ids=\(uniqueIds, privacy: .public) count=\(uniqueIds.count)")
                markBatchRead(ids: uniqueIds)
            }
            .store(in: &readCancellables)
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
                    logger.warning("[ShortNewsList] Received contentMarkedAsRead with invalid userInfo")
                    return
                }

                logger.info("[ShortNewsList] Received contentMarkedAsRead notification | contentId=\(contentId) type=\(contentType, privacy: .public)")

                // Only update if it's news content
                guard APIContentType(rawValue: contentType) == .news else {
                    logger.debug("[ShortNewsList] Ignoring non-news content | contentId=\(contentId) type=\(contentType, privacy: .public)")
                    return
                }

                logger.info("[ShortNewsList] Updating local read state from notification | contentId=\(contentId)")
                markItemsLocallyRead(ids: [contentId])
            }
            .store(in: &readCancellables)
    }

    private func markBatchRead(ids: [Int]) {
        logger.info("[ShortNewsList] markBatchRead called | ids=\(ids, privacy: .public)")

        let markedItems = markItemsLocallyRead(
            ids: ids,
            removeReadItems: false
        )
        let markedIds = markedItems.map(\.id)
        guard !markedIds.isEmpty else {
            logger.debug("[ShortNewsList] markBatchRead: all items already read, skipping")
            return
        }

        logger.info("[ShortNewsList] markBatchRead: marking \(markedIds.count) unread items | ids=\(markedIds, privacy: .public)")

        unreadCountService.decrementNewsCount(by: markedIds.count)
        logger.debug("[ShortNewsList] Decremented unread count by \(markedIds.count)")

        readRepository
            .markRead(ids: markedIds)
            .receive(on: DispatchQueue.main)
            .sink { completion in
                if case .failure(let error) = completion {
                    logger.error("[ShortNewsList] markBatchRead API failed | ids=\(markedIds, privacy: .public) error=\(error.localizedDescription)")
                }
            } receiveValue: { _ in
                logger.info("[ShortNewsList] markBatchRead API success | ids=\(markedIds, privacy: .public)")
            }
            .store(in: &readCancellables)
    }
}
