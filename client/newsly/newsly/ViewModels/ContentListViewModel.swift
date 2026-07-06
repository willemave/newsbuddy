//
//  ContentListViewModel.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation
import Observation
import SwiftUI

protocol ContentSummaryListServicing: AnyObject {
    func fetchContentList(
        contentTypes: [String]?,
        date: String?,
        readFilter: String,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse

    func fetchContentList(
        contentType: String?,
        date: String?,
        readFilter: String,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse

    func fetchKnowledgeLibrary(cursor: String?, limit: Int) async throws -> ContentListResponse
    func fetchRecentlyReadList(
        contentType: String?,
        date: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse
    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse
    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse
    func downloadMoreFromSeries(contentId: Int, count: Int) async throws -> DownloadMoreResponse
    func markContentAsUnread(id: Int) async throws
}

extension ContentService: ContentSummaryListServicing {}

@MainActor
@Observable
final class ContentListViewModel {
    private enum Mode {
        case content
        case knowledgeLibrary
        case recentlyRead
    }

    var contents: [ContentSummary] {
        get {
            readStateCache.applying(
                to: feed.items,
                removeReadItems: selectedReadFilter == "unread"
            )
        }
        set { feed.replaceItems(newValue) }
    }

    var availableDates: [String] = []
    var contentTypes: [String] = []

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

    var selectedContentType: String = "all" {
        didSet {
            Task { await reloadForCurrentFilters() }
        }
    }

    var selectedContentTypes: [String] = [] {
        didSet {
            Task { await reloadForCurrentFilters() }
        }
    }
    var selectedDate: String = "" {
        didSet {
            Task { await reloadForCurrentFilters() }
        }
    }
    var selectedReadFilter: String = "unread" {
        didSet {
            Task { await reloadForCurrentFilters() }
        }
    }

    private var mode: Mode = .content

    @ObservationIgnored
    private var feed: PaginatedFeed<ContentSummary>!

    @ObservationIgnored
    private let contentService: any ContentSummaryListServicing

    @ObservationIgnored
    private let unreadCountService: UnreadCountService

    @ObservationIgnored
    private let readStateCache: ReadStateCache

    private var actionErrorMessage: String?

    init(
        defaultReadFilter: String = "unread",
        contentService: any ContentSummaryListServicing,
        unreadCountService: UnreadCountService,
        readStateCache: ReadStateCache? = nil
    ) {
        selectedReadFilter = defaultReadFilter
        self.contentService = contentService
        self.unreadCountService = unreadCountService
        self.readStateCache = readStateCache ?? ReadStateCache()
        feed = PaginatedFeed { [weak self] cursor, generation in
            guard let self else {
                return Page(items: [], nextCursor: nil, hasMore: false)
            }
            return try await self.loadPage(cursor: cursor, generation: generation)
        }
    }
    
    /// Reload using the loader that matches the current mode, so changing a
    /// filter while in recently-read / knowledge-library mode does not silently
    /// drop back to the default list (and fire a redundant wrong-mode fetch).
    private func reloadForCurrentFilters() async {
        switch mode {
        case .recentlyRead:
            await loadRecentlyRead()
        case .knowledgeLibrary:
            await loadKnowledgeLibrary()
        case .content:
            await loadContent()
        }
    }

    func loadContent() async {
        actionErrorMessage = nil
        mode = .content
        await feed.loadInitial()
    }

    func loadMoreContent() async {
        await feed.loadNextPage()
    }
    
    func markAsRead(_ contentId: Int) async {
        do {
            guard let initialIndex = contents.firstIndex(where: { $0.id == contentId }) else { return }
            let initialContentType = contents[initialIndex].contentType
            try await readStateCache.markReadAndSync([
                ReadStateKey(id: contentId, contentType: initialContentType)
            ])

            // Re-resolve the index by id: `contents` may have been reordered or
            // shrunk by a concurrent load during the await, so the pre-await
            // index could now point at a different item (or be out of bounds).
            guard let index = contents.firstIndex(where: { $0.id == contentId }) else { return }
            let current = contents[index]
            contents[index] = current.updating(isRead: true)

            if selectedReadFilter == "unread" {
                _ = withAnimation(AppMotion.panel) {
                    contents.remove(at: index)
                }
            }
        } catch {
            actionErrorMessage = "Failed to mark as read: \(error.localizedDescription)"
        }
    }
    
    func toggleKnowledgeSave(_ contentId: Int) async {
        guard let index = contents.firstIndex(where: { $0.id == contentId }) else { return }
        let originalSavedState = contents[index].isSavedToKnowledge
        let targetSavedState = !originalSavedState
        contents[index] = contents[index].updating(isSavedToKnowledge: targetSavedState)

        do {
            if targetSavedState {
                let response = try await contentService.saveToKnowledge(id: contentId)
                if let currentIndex = contents.firstIndex(where: { $0.id == contentId }) {
                    contents[currentIndex] = contents[currentIndex].updating(
                        isSavedToKnowledge: response.isSavedToKnowledge
                    )
                }
            } else {
                let response = try await contentService.removeFromKnowledge(id: contentId)
                if let currentIndex = contents.firstIndex(where: { $0.id == contentId }) {
                    contents[currentIndex] = contents[currentIndex].updating(
                        isSavedToKnowledge: response.isSavedToKnowledge
                    )
                }
            }
        } catch {
            if let currentIndex = contents.firstIndex(where: { $0.id == contentId }),
               contents[currentIndex].isSavedToKnowledge == targetSavedState {
                contents[currentIndex] = contents[currentIndex].updating(
                    isSavedToKnowledge: originalSavedState
                )
            }
            actionErrorMessage = "Failed to update knowledge save"
        }
    }

    func loadKnowledgeLibrary() async {
        actionErrorMessage = nil
        mode = .knowledgeLibrary
        await feed.loadInitial()
    }

    func loadRecentlyRead() async {
        actionErrorMessage = nil
        mode = .recentlyRead
        await feed.loadInitial()
    }

    func refresh() async {
        await loadContent()
    }

    func markAllAsRead() async {
        let unreadItems = contents.filter { !$0.isRead }
        let unreadIds = unreadItems.map { $0.id }
        if unreadIds.isEmpty {
            return
        }

        do {
            let markedKeys = try await readStateCache.markReadAndSync(
                Set(unreadItems.map(ReadStateKey.init))
            )
            let markedSet = Set(unreadIds)

            contents = contents.map { item in
                if markedSet.contains(item.id) {
                    return item.updating(isRead: true)
                }
                return item
            }

            if selectedReadFilter == "unread" {
                withAnimation(AppMotion.panel) {
                    contents.removeAll { markedSet.contains($0.id) }
                }
            }

            if !markedKeys.isEmpty {
                await unreadCountService.refreshCounts()
            }
        } catch {
            actionErrorMessage = "Failed to mark all as read: \(error.localizedDescription)"
        }
    }

    func markAsUnreadAndRemove(_ contentId: Int) async {
        do {
            try await contentService.markContentAsUnread(id: contentId)
            withAnimation(AppMotion.panel) {
                contents.removeAll { $0.id == contentId }
            }
        } catch {
            actionErrorMessage = "Failed to mark as unread: \(error.localizedDescription)"
        }
    }

    private func loadPage(cursor: String?, generation: Int) async throws -> Page<ContentSummary> {
        let response: ContentListResponse
        switch mode {
        case .content:
            response = try await loadDefaultContentPage(cursor: cursor)
        case .knowledgeLibrary:
            response = try await contentService.fetchKnowledgeLibrary(cursor: cursor, limit: 25)
        case .recentlyRead:
            response = try await contentService.fetchRecentlyReadList(
                contentType: selectedContentType,
                date: selectedDate.isEmpty ? nil : selectedDate,
                cursor: cursor,
                limit: 25
            )
        }

        if feed.isCurrentRequest(generation) {
            availableDates = response.availableDates
            contentTypes = response.contentTypes
        }

        return Page(
            items: response.contents,
            nextCursor: response.nextCursor,
            hasMore: response.hasMore
        )
    }

    private func loadDefaultContentPage(cursor: String?) async throws -> ContentListResponse {
        if !selectedContentTypes.isEmpty {
            return try await contentService.fetchContentList(
                contentTypes: selectedContentTypes,
                date: selectedDate.isEmpty ? nil : selectedDate,
                readFilter: selectedReadFilter,
                cursor: cursor,
                limit: 25
            )
        }

        return try await contentService.fetchContentList(
            contentType: selectedContentType,
            date: selectedDate.isEmpty ? nil : selectedDate,
            readFilter: selectedReadFilter,
            cursor: cursor,
            limit: 25
        )
    }
}
