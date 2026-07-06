//
//  ShortNewsListViewModel.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Foundation
import Observation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ShortNewsList")
private let scrollReadDebounceNanoseconds: UInt64 = 300_000_000

struct ShortNewsDayGroup: Identifiable, Equatable {
    let id: String
    let calendarDayKey: String
    let delimiterItem: ContentSummary
    var items: [ContentSummary]
}

@MainActor
@Observable
final class ShortNewsListViewModel: ContentSummaryFeedEditing {
    private enum TaskKey: Hashable {
        case scrollRead
    }

    var contents: [ContentSummary] {
        get { readStateCache.applying(to: feed.items, removeReadItems: false) }
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

    var dayGroups: [ShortNewsDayGroup] {
        Self.makeDayGroups(from: currentItems())
    }

    @ObservationIgnored
    private let repository: ContentRepositoryType

    @ObservationIgnored
    private let readStateCache: ReadStateCache

    @ObservationIgnored
    private var feed: PaginatedFeed<ContentSummary>!

    @ObservationIgnored
    private let loadTasks = FeedLoadTaskRunner()

    @ObservationIgnored
    private let tasks = TaskBag<TaskKey>()

    @ObservationIgnored
    private var pendingScrollReadIds: Set<Int> = []

    private var readFilter: ReadFilter = .unread

    init(
        repository: ContentRepositoryType,
        readRepository: ReadStatusRepositoryType,
        unreadCountService: UnreadCountService,
        readStateCache: ReadStateCache? = nil
    ) {
        self.repository = repository
        self.readStateCache = readStateCache ?? ReadStateCache(
            newsReadRepository: readRepository,
            unreadCountService: unreadCountService
        )
        self.feed = PaginatedFeed(
            loadPage: { [weak self] cursor in
                guard let self else {
                    return Page(items: [], nextCursor: nil, hasMore: false)
                }
                return try await self.loadContentPage(cursor: cursor)
            },
            mergeReplacement: PaginatedFeed.mergeNewItemsOnTopKeepingExistingOrder
        )
        logger.info("[ShortNewsList] ViewModel initialized")
    }

    func refresh() async {
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

    /// Called when items have scrolled past the top of the screen
    func itemsScrolledPastTop(ids: [Int]) {
        guard !ids.isEmpty else { return }
        logger.info("[ShortNewsList] itemsScrolledPastTop | ids=\(ids, privacy: .public) count=\(ids.count)")
        pendingScrollReadIds.formUnion(ids)
        tasks.runReplacing(.scrollRead) { [weak self] in
            do {
                try await Task.sleep(nanoseconds: scrollReadDebounceNanoseconds)
            } catch {
                return
            }

            guard let self else { return }
            let idsToMark = Array(self.pendingScrollReadIds).sorted()
            self.pendingScrollReadIds.removeAll()
            guard !idsToMark.isEmpty else { return }

            logger.info("[ShortNewsList] Processing scroll-based mark read | ids=\(idsToMark, privacy: .public) count=\(idsToMark.count)")
            await self.markBatchRead(ids: idsToMark)
        }
    }

    func markRead(ids: [Int]) async {
        await markBatchRead(ids: ids)
    }

    func markAllVisibleAsRead() async {
        let unreadIds = currentItems().filter { !$0.isRead }.map(\.id)
        guard !unreadIds.isEmpty else {
            logger.debug("[ShortNewsList] markAllVisibleAsRead: no unread items")
            return
        }

        logger.info("[ShortNewsList] markAllVisibleAsRead | ids=\(unreadIds, privacy: .public) count=\(unreadIds.count)")
        await markBatchRead(ids: unreadIds)
    }

    private func loadContentPage(cursor: String?) async throws -> Page<ContentSummary> {
        let requestReadFilter = readFilter

        do {
            let response = try await repository.loadPage(
                contentTypes: [.news],
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

    private func markBatchRead(ids: [Int]) async {
        logger.info("[ShortNewsList] markBatchRead called | ids=\(ids, privacy: .public)")

        let keys = Set(
            currentItems()
                .filter { ids.contains($0.id) && !$0.isRead }
                .map(ReadStateKey.init)
        )
        guard !keys.isEmpty else {
            logger.debug("[ShortNewsList] markBatchRead: all items already read, skipping")
            return
        }

        let markedIds = keys.map(\.id)
        logger.info("[ShortNewsList] markBatchRead: marking \(markedIds.count) unread items | ids=\(markedIds, privacy: .public)")

        do {
            try await readStateCache.markReadAndSync(keys)
            logger.info("[ShortNewsList] markBatchRead API success | ids=\(markedIds, privacy: .public)")
        } catch {
            logger.error("[ShortNewsList] markBatchRead API failed | ids=\(markedIds, privacy: .public) error=\(error.localizedDescription)")
        }
    }

    private static func makeDayGroups(from items: [ContentSummary]) -> [ShortNewsDayGroup] {
        var groups: [ShortNewsDayGroup] = []

        for item in items {
            if groups.last?.calendarDayKey == item.calendarDayKey {
                groups[groups.index(before: groups.endIndex)].items.append(item)
            } else {
                groups.append(
                    ShortNewsDayGroup(
                        id: "\(item.calendarDayKey)-\(item.id)",
                        calendarDayKey: item.calendarDayKey,
                        delimiterItem: item,
                        items: [item]
                    )
                )
            }
        }

        return groups
    }
}
