//
//  ContentListViewModel.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation
import SwiftUI

@MainActor
class ContentListViewModel: CursorPaginatedViewModel {
    @Published var contents: [ContentSummary] = []
    @Published var availableDates: [String] = []
    @Published var contentTypes: [String] = []
    @Published var isLoading = false
    @Published var isLoadingMore = false
    @Published var errorMessage: String?

    // Track if we're in knowledge library mode
    private var isKnowledgeLibraryMode: Bool = false
    // Track if we're in recently read mode
    private var isRecentlyReadMode: Bool = false

    // Monotonic token used to ignore the results of superseded loads. Each fresh
    // load increments it; a load only applies its results / clears its loading
    // flag while it is still the latest request.
    private var requestGeneration = 0

    @Published var selectedContentType: String = "all" {
        didSet {
            Task { await reloadForCurrentFilters() }
        }
    }

    @Published var selectedContentTypes: [String] = [] {
        didSet {
            Task { await reloadForCurrentFilters() }
        }
    }
    @Published var selectedDate: String = "" {
        didSet {
            Task { await reloadForCurrentFilters() }
        }
    }
    @Published var selectedReadFilter: String = "unread" {
        didSet {
            Task { await reloadForCurrentFilters() }
        }
    }

    private let contentService = ContentService.shared
    private let unreadCountService = UnreadCountService.shared

    init(defaultReadFilter: String = "unread") {
        _selectedReadFilter = Published(initialValue: defaultReadFilter)
        super.init()
    }
    
    /// Reload using the loader that matches the current mode, so changing a
    /// filter while in recently-read / knowledge-library mode does not silently
    /// drop back to the default list (and fire a redundant wrong-mode fetch).
    private func reloadForCurrentFilters() async {
        if isRecentlyReadMode {
            await loadRecentlyRead()
        } else if isKnowledgeLibraryMode {
            await loadKnowledgeLibrary()
        } else {
            await loadContent()
        }
    }

    func loadContent() async {
        requestGeneration += 1
        let generation = requestGeneration
        isLoading = true
        errorMessage = nil

        // Reset pagination and special modes when loading fresh content
        isKnowledgeLibraryMode = false
        isRecentlyReadMode = false
        resetPagination()

        do {
            let response: ContentListResponse

            // Use selectedContentTypes if set, otherwise fall back to selectedContentType
            if !selectedContentTypes.isEmpty {
                response = try await contentService.fetchContentList(
                    contentTypes: selectedContentTypes,
                    date: selectedDate.isEmpty ? nil : selectedDate,
                    readFilter: selectedReadFilter,
                    cursor: nil  // Always start from beginning
                )
            } else {
                response = try await contentService.fetchContentList(
                    contentType: selectedContentType,
                    date: selectedDate.isEmpty ? nil : selectedDate,
                    readFilter: selectedReadFilter,
                    cursor: nil  // Always start from beginning
                )
            }

            guard generation == requestGeneration else { return }
            contents = response.contents
            availableDates = response.availableDates
            contentTypes = response.contentTypes
            applyPagination(response)
        } catch {
            guard generation == requestGeneration else { return }
            errorMessage = error.localizedDescription
        }

        if generation == requestGeneration {
            isLoading = false
        }
    }

    func loadMoreContent() async {
        // Don't load more if already loading or no more content
        guard !isLoadingMore, !isLoading, hasMore, let cursor = nextCursor else {
            return
        }

        // Capture (do not bump) the current generation: paging extends the
        // active list, so a fresh load started meanwhile should discard our page.
        let generation = requestGeneration
        isLoadingMore = true

        do {
            let response: ContentListResponse

            if isKnowledgeLibraryMode {
                response = try await contentService.fetchKnowledgeLibrary(cursor: cursor)
            } else if isRecentlyReadMode {
                response = try await contentService.fetchRecentlyReadList(
                    contentType: selectedContentType,
                    date: selectedDate.isEmpty ? nil : selectedDate,
                    cursor: cursor
                )
            } else {
                // Use selectedContentTypes if set, otherwise fall back to selectedContentType
                if !selectedContentTypes.isEmpty {
                    response = try await contentService.fetchContentList(
                        contentTypes: selectedContentTypes,
                        date: selectedDate.isEmpty ? nil : selectedDate,
                        readFilter: selectedReadFilter,
                        cursor: cursor
                    )
                } else {
                    response = try await contentService.fetchContentList(
                        contentType: selectedContentType,
                        date: selectedDate.isEmpty ? nil : selectedDate,
                        readFilter: selectedReadFilter,
                        cursor: cursor
                    )
                }
            }

            // Append new contents to existing list, unless a fresh load
            // superseded us during the await.
            if generation == requestGeneration {
                contents.append(contentsOf: response.contents)
                applyPagination(response)
            }
        } catch {
            if generation == requestGeneration {
                errorMessage = error.localizedDescription
            }
        }

        isLoadingMore = false
    }
    
    func markAsRead(_ contentId: Int) async {
        do {
            guard let initialIndex = contents.firstIndex(where: { $0.id == contentId }) else { return }
            let initialContentType = contents[initialIndex].apiContentType
            try await contentService.markContentAsRead(id: contentId, contentType: initialContentType)

            // Re-resolve the index by id: `contents` may have been reordered or
            // shrunk by a concurrent load during the await, so the pre-await
            // index could now point at a different item (or be out of bounds).
            guard let index = contents.firstIndex(where: { $0.id == contentId }) else { return }
            let current = contents[index]
            let shouldDecrementUnreadCount = !current.isRead
            contents[index] = current.updating(isRead: true)

            if shouldDecrementUnreadCount {
                switch current.apiContentType {
                case .article?:
                    unreadCountService.decrementArticleCount()
                case .podcast?:
                    unreadCountService.decrementPodcastCount()
                case .news?:
                    unreadCountService.decrementNewsCount()
                default:
                    break
                }
            }

            if selectedReadFilter == "unread" {
                _ = withAnimation(.easeOut(duration: 0.3)) {
                    contents.remove(at: index)
                }
            }
        } catch {
            errorMessage = "Failed to mark as read: \(error.localizedDescription)"
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
            errorMessage = "Failed to update knowledge save"
        }
    }

    func loadKnowledgeLibrary() async {
        requestGeneration += 1
        let generation = requestGeneration
        isLoading = true
        errorMessage = nil

        isKnowledgeLibraryMode = true
        isRecentlyReadMode = false
        resetPagination()

        do {
            let response = try await contentService.fetchKnowledgeLibrary(cursor: nil)
            guard generation == requestGeneration else { return }
            contents = response.contents
            availableDates = response.availableDates
            contentTypes = response.contentTypes
            applyPagination(response)
        } catch {
            guard generation == requestGeneration else { return }
            errorMessage = error.localizedDescription
        }

        if generation == requestGeneration {
            isLoading = false
        }
    }

    func loadRecentlyRead() async {
        requestGeneration += 1
        let generation = requestGeneration
        isLoading = true
        errorMessage = nil

        isKnowledgeLibraryMode = false
        isRecentlyReadMode = true
        resetPagination()

        do {
            let response = try await contentService.fetchRecentlyReadList(
                contentType: selectedContentType,
                date: selectedDate.isEmpty ? nil : selectedDate,
                cursor: nil
            )
            guard generation == requestGeneration else { return }
            contents = response.contents
            availableDates = response.availableDates
            contentTypes = response.contentTypes
            applyPagination(response)
        } catch {
            guard generation == requestGeneration else { return }
            errorMessage = error.localizedDescription
        }

        if generation == requestGeneration {
            isLoading = false
        }
    }

    func refresh() async {
        // Reset pagination and reload
        resetPagination()
        await loadContent()
    }

    func markAllAsRead() async {
        let unreadItems = contents.filter { !$0.isRead }
        let unreadIds = unreadItems.map { $0.id }
        if unreadIds.isEmpty {
            return
        }

        do {
            let response = try await contentService.bulkMarkAsRead(contentIds: unreadIds)
            let markedSet = Set(unreadIds)

            contents = contents.map { item in
                if markedSet.contains(item.id) {
                    return item.updating(isRead: true)
                }
                return item
            }

            if selectedReadFilter == "unread" {
                withAnimation(.easeOut(duration: 0.3)) {
                    contents.removeAll { markedSet.contains($0.id) }
                }
            }

            if response.markedCount > 0 {
                // Count how many of each type were marked
                var articleCount = 0
                var podcastCount = 0
                var newsCount = 0

                for item in unreadItems {
                    if markedSet.contains(item.id) {
                        switch item.contentType {
                        case "article":
                            articleCount += 1
                        case "podcast":
                            podcastCount += 1
                        case "news":
                            newsCount += 1
                        default:
                            break
                        }
                    }
                }

                // Decrement counts for each type
                if articleCount > 0 {
                    unreadCountService.decrementArticleCount(by: articleCount)
                }
                if podcastCount > 0 {
                    unreadCountService.decrementPodcastCount(by: podcastCount)
                }
                if newsCount > 0 {
                    unreadCountService.decrementNewsCount(by: newsCount)
                }
            }
        } catch {
            errorMessage = "Failed to mark all as read: \(error.localizedDescription)"
        }
    }
}
