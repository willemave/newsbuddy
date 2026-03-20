//
//  SearchViewModel.swift
//  newsly
//
//  Created by Assistant on 9/15/25.
//

import Combine
import Foundation

@MainActor
final class SearchViewModel: ObservableObject {
    @Published var searchText: String = ""
    @Published var contentResults: [ContentSummary] = []
    @Published var feedResults: [MixedSearchFeedResult] = []
    @Published var podcastResults: [PodcastSearchResult] = []
    @Published var isLoadingLocal: Bool = false
    @Published var isLoadingMixed: Bool = false
    @Published var actionInFlightIds: Set<String> = []
    @Published var completedActionIds: Set<String> = []
    @Published var errorMessage: String?
    @Published var hasLocalSearch: Bool = false
    @Published var hasSubmittedSearch: Bool = false

    private let contentService = ContentService.shared
    private let scraperConfigService = ScraperConfigService.shared
    private var cancellables = Set<AnyCancellable>()
    private var localSearchTask: Task<Void, Never>?
    private var mixedSearchTask: Task<Void, Never>?
    private var lastSubmittedQuery: String?

    init() {
        $searchText
            .debounce(for: .milliseconds(350), scheduler: RunLoop.main)
            .removeDuplicates()
            .sink { [weak self] text in
                self?.handleQueryChanged(text)
            }
            .store(in: &cancellables)
    }

    var trimmedQuery: String {
        searchText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var hasQuery: Bool {
        trimmedQuery.count >= 2
    }

    func retrySearch() {
        if hasSubmittedSearch {
            submitSearch()
            return
        }
        let query = trimmedQuery
        guard query.count >= 2 else { return }
        localSearchTask?.cancel()
        localSearchTask = Task { [weak self] in
            await self?.runLocalSearchTask(for: query)
        }
    }

    func submitSearch() {
        let query = trimmedQuery
        guard query.count >= 2 else {
            errorMessage = "Type at least 2 characters to search."
            return
        }

        mixedSearchTask?.cancel()
        mixedSearchTask = Task { [weak self] in
            await self?.runMixedSearch(for: query)
        }
    }

    func subscribeToFeed(_ result: MixedSearchFeedResult) async {
        let actionId = "feed:\(result.id)"
        await runAction(id: actionId) {
            _ = try await self.scraperConfigService.subscribeFeed(
                feedURL: result.feedURL,
                feedType: result.feedType,
                displayName: result.title
            )
        }
    }

    func addPodcastEpisode(_ result: PodcastSearchResult) async {
        let actionId = "episode:\(result.id)"
        guard let url = URL(string: result.episodeURL) else {
            errorMessage = "Invalid episode URL"
            return
        }
        await runAction(id: actionId) {
            _ = try await self.contentService.submitContent(url: url, title: result.title)
        }
    }

    func subscribeToPodcast(_ result: PodcastSearchResult) async {
        guard let feedURL = result.feedURL else { return }
        let actionId = "podcast-feed:\(feedURL)"
        await runAction(id: actionId) {
            _ = try await self.scraperConfigService.subscribeFeed(
                feedURL: feedURL,
                feedType: "podcast_rss",
                displayName: result.podcastTitle ?? result.title
            )
        }
    }

    private func handleQueryChanged(_ query: String) {
        localSearchTask?.cancel()
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)

        if lastSubmittedQuery != trimmed {
            hasSubmittedSearch = false
            feedResults = []
            podcastResults = []
            completedActionIds = []
        }

        guard trimmed.count >= 2 else {
            contentResults = []
            hasLocalSearch = false
            errorMessage = nil
            return
        }

        localSearchTask = Task { [weak self] in
            await self?.runLocalSearchTask(for: trimmed)
        }
    }

    private func runLocalSearchTask(for query: String) async {
        isLoadingLocal = true
        errorMessage = nil

        do {
            let response = try await contentService.searchContent(
                query: query,
                contentType: "all",
                limit: 25,
                cursor: nil
            )
            guard !Task.isCancelled else { return }
            contentResults = response.contents
            hasLocalSearch = true
        } catch {
            guard !Task.isCancelled else { return }
            errorMessage = error.localizedDescription
            contentResults = []
            hasLocalSearch = true
        }

        isLoadingLocal = false
    }

    private func runMixedSearch(for query: String) async {
        isLoadingMixed = true
        errorMessage = nil

        do {
            let response = try await contentService.searchMixed(query: query, limit: 10)
            guard !Task.isCancelled else { return }
            lastSubmittedQuery = query
            contentResults = response.content
            feedResults = response.feeds
            podcastResults = response.podcasts
            hasLocalSearch = true
            hasSubmittedSearch = true
        } catch {
            guard !Task.isCancelled else { return }
            errorMessage = error.localizedDescription
            feedResults = []
            podcastResults = []
            hasSubmittedSearch = true
        }

        isLoadingMixed = false
    }

    private func runAction(
        id: String,
        action: @escaping () async throws -> Void
    ) async {
        actionInFlightIds.insert(id)
        defer { actionInFlightIds.remove(id) }

        do {
            try await action()
            completedActionIds.insert(id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
