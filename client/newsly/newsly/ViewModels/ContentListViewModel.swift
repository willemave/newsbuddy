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

    private enum ContentMutation {
        case savedToKnowledge(Bool)
        case removedFromRecentlyRead
    }

    private struct VersionedContentMutation {
        let version: Int
        let mutation: ContentMutation
    }

    var contents: [ContentSummary] {
        get {
            readStateCache.applying(
                to: feed.items,
                removeReadItems: false
            )
        }
        set {
            feed.replaceItems(newValue)
            refreshReadyContentIDs()
        }
    }

    var availableDates: [String] = []
    var contentTypes: [String] = []
    private(set) var readyContentIDs: [Int] = []

    var isLoading: Bool {
        feed.phase == .initialLoading
    }

    var isLoadingMore: Bool {
        feed.phase == .loadingMore
    }

    var hasMoreContent: Bool {
        feed.hasMore
    }

    var errorMessage: String? {
        if let actionErrorMessage {
            return actionErrorMessage
        }
        return loadErrorMessage
    }

    var loadErrorMessage: String? {
        initialLoadErrorMessage ?? loadMoreErrorMessage
    }

    private(set) var initialLoadErrorMessage: String?
    private(set) var loadMoreErrorMessage: String?

    var hasActionError: Bool {
        actionErrorMessage != nil
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

    private(set) var actionErrorMessage: String?
    @ObservationIgnored
    private var contentMutations: [Int: VersionedContentMutation] = [:]
    @ObservationIgnored
    private var contentMutationVersion = 0

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
        loadMoreErrorMessage = nil
        await feed.loadNextPage()
        loadMoreErrorMessage = currentFeedErrorMessage
        refreshReadyContentIDs()
    }
    
    func toggleKnowledgeSave(_ contentId: Int) async {
        guard let index = contents.firstIndex(where: { $0.id == contentId }) else { return }
        actionErrorMessage = nil
        let originalSavedState = contents[index].isSavedToKnowledge
        let targetSavedState = !originalSavedState
        recordContentMutation(.savedToKnowledge(targetSavedState), for: contentId)
        contents[index] = contents[index].updating(isSavedToKnowledge: targetSavedState)

        do {
            if targetSavedState {
                let response = try await contentService.saveToKnowledge(id: contentId)
                recordContentMutation(
                    .savedToKnowledge(response.isSavedToKnowledge),
                    for: contentId
                )
                if let currentIndex = contents.firstIndex(where: { $0.id == contentId }) {
                    contents[currentIndex] = contents[currentIndex].updating(
                        isSavedToKnowledge: response.isSavedToKnowledge
                    )
                }
            } else {
                let response = try await contentService.removeFromKnowledge(id: contentId)
                recordContentMutation(
                    .savedToKnowledge(response.isSavedToKnowledge),
                    for: contentId
                )
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
            recordContentMutation(.savedToKnowledge(originalSavedState), for: contentId)
            actionErrorMessage = "Couldn't update this save. Try the action again."
        }
    }

    func loadKnowledgeLibrary(query: String? = nil) async {
        actionErrorMessage = nil
        initialLoadErrorMessage = nil
        loadMoreErrorMessage = nil
        mode = .knowledgeLibrary
        let trimmedQuery = query?.trimmingCharacters(in: .whitespacesAndNewlines)
        knowledgeQuery = trimmedQuery?.isEmpty == false ? trimmedQuery : nil
        await feed.loadInitial()
        initialLoadErrorMessage = currentFeedErrorMessage
        refreshReadyContentIDs()
    }

    /// Revalidates the first Knowledge page while preserving the current list.
    func revalidateKnowledgeLibrary() async {
        initialLoadErrorMessage = nil
        loadMoreErrorMessage = nil
        mode = .knowledgeLibrary
        knowledgeQuery = nil
        await feed.refresh()
        initialLoadErrorMessage = currentFeedErrorMessage
        refreshReadyContentIDs()
    }

    func cancelAutomaticRead() {
        feed.cancelRequestRetainingState()
    }

    func clearKnowledgeLibrary() {
        mode = .knowledgeLibrary
        knowledgeQuery = nil
        actionErrorMessage = nil
        initialLoadErrorMessage = nil
        loadMoreErrorMessage = nil
        feed.reset()
        refreshReadyContentIDs()
    }

    func clearActionError() {
        actionErrorMessage = nil
    }

    func loadRecentlyRead() async {
        actionErrorMessage = nil
        initialLoadErrorMessage = nil
        loadMoreErrorMessage = nil
        mode = .recentlyRead
        await feed.loadInitial()
        initialLoadErrorMessage = currentFeedErrorMessage
        refreshReadyContentIDs()
    }

    func markAsUnreadAndRemove(_ contentId: Int) async {
        actionErrorMessage = nil
        do {
            try await contentService.markContentAsUnread(id: contentId)
            recordContentMutation(.removedFromRecentlyRead, for: contentId)
            withAnimation(AppMotion.panel) {
                contents.removeAll { $0.id == contentId }
            }
        } catch {
            actionErrorMessage = "Failed to mark as unread: \(error.localizedDescription)"
        }
    }

    private func loadPage(cursor: String?, generation: Int) async throws -> Page<ContentSummary> {
        let requestMode = mode
        let mutationVersionAtRequestStart = contentMutationVersion
        let response: ContentListResponse
        switch requestMode {
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

        let reconciledContents = reconcileLoadedContents(
            response.contents,
            requestMode: requestMode,
            isAuthoritativeFirstPage: cursor == nil && feed.isCurrentRequest(generation),
            mutationVersionAtRequestStart: mutationVersionAtRequestStart
        )

        return Page(
            items: reconciledContents,
            nextCursor: response.nextCursor,
            hasMore: response.hasMore
        )
    }

    private func reconcileLoadedContents(
        _ loadedContents: [ContentSummary],
        requestMode: Mode,
        isAuthoritativeFirstPage: Bool,
        mutationVersionAtRequestStart: Int
    ) -> [ContentSummary] {
        var reconciled = loadedContents

        for (contentID, entry) in Array(contentMutations) {
            switch entry.mutation {
            case .savedToKnowledge(let expectedState):
                guard let index = reconciled.firstIndex(where: { $0.id == contentID }) else {
                    if requestMode == .knowledgeLibrary,
                       isAuthoritativeFirstPage,
                       !expectedState,
                       entry.version <= mutationVersionAtRequestStart {
                        retireContentMutation(contentID, version: entry.version)
                    }
                    continue
                }
                if isAuthoritativeFirstPage,
                   entry.version <= mutationVersionAtRequestStart,
                   reconciled[index].isSavedToKnowledge == expectedState {
                    retireContentMutation(contentID, version: entry.version)
                } else {
                    reconciled[index] = reconciled[index].updating(
                        isSavedToKnowledge: expectedState
                    )
                }
            case .removedFromRecentlyRead:
                guard requestMode == .recentlyRead else { continue }
                let containedRemovedItem = reconciled.contains { $0.id == contentID }
                reconciled.removeAll { $0.id == contentID }
                if isAuthoritativeFirstPage,
                   !containedRemovedItem,
                   entry.version <= mutationVersionAtRequestStart {
                    retireContentMutation(contentID, version: entry.version)
                }
            }
        }
        return reconciled
    }

    private func recordContentMutation(_ mutation: ContentMutation, for contentID: Int) {
        contentMutationVersion += 1
        contentMutations[contentID] = VersionedContentMutation(
            version: contentMutationVersion,
            mutation: mutation
        )
    }

    private func retireContentMutation(_ contentID: Int, version: Int) {
        guard contentMutations[contentID]?.version == version else { return }
        contentMutations.removeValue(forKey: contentID)
    }

    private func refreshReadyContentIDs() {
        let updatedIDs = feed.items.compactMap { content in
            content.savedLibraryItemState == .ready ? content.id : nil
        }
        guard readyContentIDs != updatedIDs else { return }
        readyContentIDs = updatedIDs
    }

    private var currentFeedErrorMessage: String? {
        guard case .error(let message) = feed.phase else { return nil }
        return message
    }
}
