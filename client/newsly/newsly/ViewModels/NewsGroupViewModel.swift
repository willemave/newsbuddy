//
//  NewsGroupViewModel.swift
//  newsly
//
//  Created by Assistant on 10/12/25.
//

import Foundation
import SwiftUI

@MainActor
class NewsGroupViewModel: CursorPaginatedViewModel {
    @Published var newsGroups: [NewsGroup] = []
    @Published var isLoading = false
    @Published var isLoadingMore = false
    @Published var errorMessage: String?

    private let contentService = ContentService.shared
    private let unreadCountService = UnreadCountService.shared

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
        isLoading = true
        errorMessage = nil
        resetPagination()

        if !preserveReadGroups {
            sessionReadGroupIds.removeAll()
        }

        let preservedReads = preserveReadGroups ? newsGroups.filter { $0.isRead } : []

        do {
            // Load news content (limit = groupSize * 5 groups)
            let limit = groupSize * 5
            print("🧮 Fetch news groups — size: \(groupSize), limit: \(limit), preserve reads: \(preserveReadGroups)")
            let response = try await contentService.fetchContentList(
                contentType: "news",
                date: nil,
                readFilter: "unread",
                cursor: nil,
                limit: limit
            )

            // Group items to fit the actual card height when metrics are available
            var fetchedGroups: [NewsGroup]
            if let h = groupingAvailableHeight, let w = groupingTextWidth, h > 0, w > 0 {
                fetchedGroups = response.contents.groupedToFit(availableHeight: h, textWidth: w)
            } else {
                fetchedGroups = response.contents.grouped(by: groupSize)
            }
            let groupSizes = fetchedGroups.map { $0.items.count }
            print("🧮 Fetch returned \(response.contents.count) items → \(fetchedGroups.count) groups with sizes \(groupSizes)")

            if preserveReadGroups, !preservedReads.isEmpty {
                // Keep current-session reads visible while fetching new data
                for group in preservedReads where !fetchedGroups.contains(where: { $0.id == group.id }) {
                    fetchedGroups.append(group)
                }
            }

            newsGroups = fetchedGroups
            applyPagination(response)
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func loadMoreGroups() async {
        guard !isLoadingMore, !isLoading, hasMore, let cursor = nextCursor else {
            return
        }

        isLoadingMore = true

        do {
            // Load more with same dynamic limit
            let limit = groupSize * 5
            let response = try await contentService.fetchContentList(
                contentType: "news",
                date: nil,
                readFilter: "unread",
                cursor: cursor,
                limit: limit
            )

            // Append new groups using the same height-aware packing
            let newGroups: [NewsGroup]
            if let h = groupingAvailableHeight, let w = groupingTextWidth, h > 0, w > 0 {
                newGroups = response.contents.groupedToFit(availableHeight: h, textWidth: w)
            } else {
                newGroups = response.contents.grouped(by: groupSize)
            }
            newsGroups.append(contentsOf: newGroups)
            applyPagination(response)
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoadingMore = false
    }

    func markGroupAsRead(_ groupId: String) async {
        guard let groupIndex = newsGroups.firstIndex(where: { $0.id == groupId }) else {
            return
        }

        let group = newsGroups[groupIndex]
        let itemIds = group.items.map { $0.id }

        do {
            _ = try await contentService.bulkMarkAsRead(contentIds: itemIds)

            // Update local state to mark as read while keeping it visible this session
            newsGroups[groupIndex] = group.updatingAllAsRead(true)

            sessionReadGroupIds.insert(groupId)

            // Update unread counts
            unreadCountService.decrementNewsCount(by: itemIds.count)

            // Items stay in memory during a session; ShortFormView clears them on tab exit
        } catch {
            ToastService.shared.showError("Failed to mark as read")
            errorMessage = "Failed to mark group as read: \(error.localizedDescription)"
        }
    }

    func preloadNextGroups() async {
        // Trigger load when down to 2 unread groups
        let unreadCount = newsGroups.filter { !$0.isRead }.count
        if unreadCount <= 2 && !isLoadingMore && hasMore {
            await loadMoreGroups()
        }
    }

    func toggleFavorite(_ contentId: Int) async {
        // Find group and item
        guard let groupIndex = newsGroups.firstIndex(where: { $0.items.contains(where: { $0.id == contentId }) }) else {
            return
        }

        let group = newsGroups[groupIndex]
        guard group.items.contains(where: { $0.id == contentId }) else {
            return
        }

        // Optimistically update
        newsGroups[groupIndex] = group.updatingItem(id: contentId) { item in
            item.updating(isFavorited: !item.isFavorited)
        }

        do {
            let response = try await contentService.toggleFavorite(id: contentId)

            // Update with server response
            if let isFavorited = response["is_favorited"] as? Bool {
                newsGroups[groupIndex] = group.updatingItem(id: contentId) { item in
                    item.updating(isFavorited: isFavorited)
                }
            }
        } catch {
            // Revert on error
            newsGroups[groupIndex] = group.updatingItem(id: contentId) { item in
                item.updating(isFavorited: !item.isFavorited)
            }
            errorMessage = "Failed to toggle favorite"
        }
    }

    func refresh() async {
        resetPagination()
        await loadNewsGroups(preserveReadGroups: true)
    }

    func clearSessionReads() {
        guard !newsGroups.isEmpty else {
            sessionReadGroupIds.removeAll()
            return
        }

        let idsToRemove = sessionReadGroupIds
        newsGroups.removeAll { idsToRemove.contains($0.id) || $0.isRead }
        sessionReadGroupIds.removeAll()
    }
}
