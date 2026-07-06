//
//  SearchViewModel.swift
//  newsly
//
//  Created by Assistant on 9/15/25.
//

import Foundation
import Observation

@MainActor
@Observable
final class SearchViewModel {
    private enum TaskKey: Hashable {
        case mixed
    }

    // Keep the auto-updating local search compact so each keystroke does less work.
    private let localSearchResultLimit = 10

    var searchText: String = ""
    var contentResults: [ContentSummary] = []
    var feedResults: [MixedSearchFeedResult] = []
    var podcastResults: [PodcastSearchResult] = []
    var isLoadingLocal: Bool = false
    var isLoadingMixed: Bool = false
    var actionInFlightIds: Set<String> = []
    var completedActionIds: Set<String> = []
    var errorMessage: String?
    var hasLocalSearch: Bool = false
    var hasSubmittedSearch: Bool = false

    @ObservationIgnored
    private let contentService: ContentService

    @ObservationIgnored
    private let scraperConfigService: ScraperConfigService

    @ObservationIgnored
    private let tasks = TaskBag<TaskKey>()

    @ObservationIgnored
    private var localSearchGeneration = 0

    private var lastSubmittedQuery: String?

    init(
        contentService: ContentService,
        scraperConfigService: ScraperConfigService
    ) {
        self.contentService = contentService
        self.scraperConfigService = scraperConfigService
    }

    deinit {
        tasks.cancelAll()
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
        localSearchGeneration += 1
        let generation = localSearchGeneration
        Task { [weak self] in
            await self?.runLocalSearchTask(for: query, generation: generation)
        }
    }

    func submitSearch() {
        let query = trimmedQuery
        guard query.count >= 2 else {
            errorMessage = "Type at least 2 characters to search."
            return
        }

        tasks.runReplacing(.mixed) { [weak self] in
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

    func handleSearchTextChangedAfterDelay() async {
        localSearchGeneration += 1
        let generation = localSearchGeneration
        let trimmed = trimmedQuery

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
            isLoadingLocal = false
            return
        }

        do {
            try await Task.sleep(for: .milliseconds(350))
        } catch {
            return
        }

        guard !Task.isCancelled, generation == localSearchGeneration else { return }
        await runLocalSearchTask(for: trimmed, generation: generation)
    }

    private func runLocalSearchTask(for query: String, generation: Int) async {
        isLoadingLocal = true
        defer {
            if generation == localSearchGeneration {
                isLoadingLocal = false
            }
        }
        errorMessage = nil

        do {
            let response = try await contentService.searchContent(
                query: query,
                contentType: "all",
                limit: localSearchResultLimit,
                cursor: nil
            )
            guard !Task.isCancelled, generation == localSearchGeneration else { return }
            contentResults = response.contents
            hasLocalSearch = true
        } catch {
            guard !Task.isCancelled, generation == localSearchGeneration else { return }
            errorMessage = error.localizedDescription
            contentResults = []
            hasLocalSearch = true
        }
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
