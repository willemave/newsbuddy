//
//  NewsGroupViewModel.swift
//  newsly
//
//  Created by Assistant on 10/12/25.
//

import Foundation
import Observation
import SwiftUI

@MainActor
@Observable
final class NewsGroupViewModel {
    var newsGroups: [NewsGroup] {
        get { feed.items }
        set { feed.replaceItems(newValue) }
    }

    var isLoading: Bool {
        feed.phase == .initialLoading
    }

    var isLoadingMore: Bool {
        feed.phase == .loadingMore
    }

    var errorMessage: String? {
        if let actionErrorMessage {
            return actionErrorMessage
        }
        guard case .error(let message) = feed.phase else { return nil }
        return message
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
    private let readRepository: ReadStatusRepositoryType

    @ObservationIgnored
    private let unreadCountService: UnreadCountService

    @ObservationIgnored
    private let toastPresenter: any ToastPresenting

    @ObservationIgnored
    private var feed: PaginatedFeed<NewsGroup>!

    private var actionErrorMessage: String?

    init(
        repository: ContentRepositoryType = ContentRepository(includeAvailableDates: false),
        readRepository: ReadStatusRepositoryType = ReadStatusRepository(endpoint: .newsItems),
        unreadCountService: UnreadCountService,
        toastPresenter: any ToastPresenting
    ) {
        self.repository = repository
        self.readRepository = readRepository
        self.unreadCountService = unreadCountService
        self.toastPresenter = toastPresenter
        self.feed = PaginatedFeed { [weak self] cursor in
            guard let self else {
                return Page(items: [], nextCursor: nil, hasMore: false)
            }
            return try await self.loadGroupedNewsPage(cursor: cursor)
        }
    }

    @ObservationIgnored
    private var sessionReadGroupIds: Set<String> = []

    // Dynamic group size based on screen height
    var groupSize: Int = 7  // Default, will be updated by view

    // Metrics from the view to enable height-aware grouping
    var groupingAvailableHeight: CGFloat?
    var groupingTextWidth: CGFloat?

    func setGroupingMetrics(contentWidth: CGFloat, availableHeight: CGFloat) {
        groupingTextWidth = contentWidth
        groupingAvailableHeight = availableHeight
    }

    func loadNewsGroups(preserveReadGroups: Bool = false) async {
        actionErrorMessage = nil

        if !preserveReadGroups {
            sessionReadGroupIds.removeAll()
        }

        let preservedReads = preserveReadGroups ? newsGroups.filter { $0.isRead } : []

        await feed.loadInitial()

        guard preserveReadGroups, !preservedReads.isEmpty else { return }
        var fetchedGroups = newsGroups
        for group in preservedReads where !fetchedGroups.contains(where: { $0.id == group.id }) {
            fetchedGroups.append(group)
        }
        newsGroups = fetchedGroups
    }

    func loadMoreGroups() async {
        guard !isLoadingMore, !isLoading, hasMore, nextCursor != nil else {
            return
        }

        actionErrorMessage = nil
        await feed.loadNextPage()
    }

    func markGroupAsRead(_ groupId: String) async {
        guard let groupIndex = newsGroups.firstIndex(where: { $0.id == groupId }) else {
            return
        }

        let group = newsGroups[groupIndex]
        let itemIds = group.items.map { $0.id }

        do {
            try await markNewsItemsAsRead(itemIds)

            // Update local state to mark as read while keeping it visible this session
            var nextGroups = newsGroups
            nextGroups[groupIndex] = group.updatingAllAsRead(true)
            newsGroups = nextGroups

            sessionReadGroupIds.insert(groupId)

            // Update unread counts
            unreadCountService.decrementNewsCount(by: itemIds.count)

            // Items stay in memory during a session; ShortFormView clears them on tab exit
        } catch {
            toastPresenter.showError("Failed to mark as read")
            actionErrorMessage = "Failed to mark group as read: \(error.localizedDescription)"
        }
    }

    private func loadNewsPage(cursor: String?, limit: Int) async throws -> ContentListResponse {
        try await repository.loadPage(
            contentTypes: [.news],
            readFilter: .unread,
            cursor: cursor,
            limit: limit
        )
    }

    private func loadGroupedNewsPage(cursor: String?) async throws -> Page<NewsGroup> {
        let limit = groupSize * 5
        print("🧮 Fetch news groups — size: \(groupSize), limit: \(limit)")
        let response = try await loadNewsPage(cursor: cursor, limit: limit)
        let groups = group(response.contents)
        let groupSizes = groups.map { $0.items.count }
        print("🧮 Fetch returned \(response.contents.count) items → \(groups.count) groups with sizes \(groupSizes)")
        return Page(items: groups, nextCursor: response.nextCursor, hasMore: response.hasMore)
    }

    private func group(_ contents: [ContentSummary]) -> [NewsGroup] {
        if let h = groupingAvailableHeight, let w = groupingTextWidth, h > 0, w > 0 {
            return contents.groupedToFit(availableHeight: h, textWidth: w)
        }
        return contents.grouped(by: groupSize)
    }

    private func markNewsItemsAsRead(_ itemIds: [Int]) async throws {
        try await readRepository.markRead(ids: itemIds)
    }

    func preloadNextGroups() async {
        // Trigger load when down to 2 unread groups
        let unreadCount = newsGroups.filter { !$0.isRead }.count
        if unreadCount <= 2 && !isLoadingMore && hasMore {
            await loadMoreGroups()
        }
    }

    func refresh() async {
        await loadNewsGroups(preserveReadGroups: true)
    }

    func clearSessionReads() {
        guard !newsGroups.isEmpty else {
            sessionReadGroupIds.removeAll()
            return
        }

        let idsToRemove = sessionReadGroupIds
        newsGroups = newsGroups.filter { !idsToRemove.contains($0.id) && !$0.isRead }
        sessionReadGroupIds.removeAll()
    }
}
