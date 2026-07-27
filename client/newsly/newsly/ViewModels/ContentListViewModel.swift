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
    func fetchKnowledgeLibrary(
        query: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse
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
        case knowledgeLibrary
        case recentlyRead
    }

    var contents: [ContentSummary] {
        get {
            readStateCache.applying(
                to: feed.items,
                removeReadItems: false
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

    var selectedContentType: String = "all" {
        didSet {
            Task { await reloadForCurrentFilters() }
        }
    }

    var selectedDate: String = "" {
        didSet {
            Task { await reloadForCurrentFilters() }
        }
    }

    private var mode: Mode = .knowledgeLibrary
    private var knowledgeQuery: String?

    @ObservationIgnored
    private var feed: PaginatedFeed<ContentSummary>!

    @ObservationIgnored
    private let contentService: any ContentSummaryListServicing

    @ObservationIgnored
    private let readStateCache: ReadStateCache

    private var actionErrorMessage: String?

    init(
        contentService: any ContentSummaryListServicing,
        readStateCache: ReadStateCache? = nil
    ) {
        self.contentService = contentService
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
            await loadKnowledgeLibrary(query: knowledgeQuery)
        }
    }

    func loadMoreContent() async {
        await feed.loadNextPage()
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

    func loadKnowledgeLibrary(query: String? = nil) async {
        actionErrorMessage = nil
        mode = .knowledgeLibrary
        let trimmedQuery = query?.trimmingCharacters(in: .whitespacesAndNewlines)
        knowledgeQuery = trimmedQuery?.isEmpty == false ? trimmedQuery : nil
        await feed.loadInitial()
    }

    func clearKnowledgeLibrary() {
        mode = .knowledgeLibrary
        knowledgeQuery = nil
        actionErrorMessage = nil
        feed.reset()
    }

    func loadRecentlyRead() async {
        actionErrorMessage = nil
        mode = .recentlyRead
        await feed.loadInitial()
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
        case .knowledgeLibrary:
            response = try await contentService.fetchKnowledgeLibrary(
                query: knowledgeQuery,
                cursor: cursor,
                limit: 25
            )
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
}
