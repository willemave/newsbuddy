//
//  SearchViewModel.swift
//  newsly
//
//  Created by Assistant on 9/15/25.
//

import Foundation
import Observation

enum SearchActionTarget: Hashable {
    case feed(String)
    case episode(String)
    case podcastFeed(String)
}

struct SearchActionState: Equatable {
    let isInFlight: Bool
    let completedLabel: String?
    let errorMessage: String?
}

@MainActor
protocol SearchContentServicing: AnyObject {
    func searchContent(
        query: String,
        contentType: String,
        limit: Int,
        cursor: String?
    ) async throws -> ContentListResponse

    func searchMixed(query: String, limit: Int) async throws -> MixedSearchResponse

    func submitContent(
        url: URL,
        title: String?,
        platform: String?
    ) async throws -> SubmitContentResponse
}

extension ContentService: SearchContentServicing {}

extension ScraperConfigService: FeedSubscribing {}

@MainActor
@Observable
final class SearchViewModel {
    private enum TaskKey: Hashable {
        case local
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
    private var actionTargetsInFlight: Set<SearchActionTarget> = []
    private var completedActionLabels: [SearchActionTarget: String] = [:]
    var localErrorMessage: String?
    var mixedErrorMessage: String?
    private var actionErrorMessages: [SearchActionTarget: String] = [:]
    var hasLocalSearch: Bool = false
    var hasSubmittedSearch: Bool = false

    @ObservationIgnored
    private let contentService: any SearchContentServicing

    @ObservationIgnored
    private let scraperConfigService: any FeedSubscribing

    @ObservationIgnored
    private let tasks = TaskBag<TaskKey>()

    @ObservationIgnored
    private var observedQuery: String?

    init(
        contentService: any SearchContentServicing,
        scraperConfigService: any FeedSubscribing
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

    func actionState(for target: SearchActionTarget) -> SearchActionState {
        SearchActionState(
            isInFlight: actionTargetsInFlight.contains(target),
            completedLabel: completedActionLabels[target],
            errorMessage: actionErrorMessages[target]
        )
    }

    func retrySearch() {
        if hasSubmittedSearch || mixedErrorMessage != nil {
            submitSearch()
            return
        }
        let query = trimmedQuery
        guard query.count >= 2 else { return }
        startLocalSearch(for: query, debounce: false)
    }

    func submitSearch() {
        let query = trimmedQuery
        guard query.count >= 2 else {
            localErrorMessage = "Type at least 2 characters to search."
            return
        }

        tasks.runReplacing(.mixed) { [weak self] token in
            await self?.runMixedSearch(for: query, token: token)
        }
    }

    func subscribeToFeed(_ result: MixedSearchFeedResult) async {
        let target = SearchActionTarget.feed(result.id)
        guard !result.isSubscribed, completedActionLabels[target] == nil else { return }
        await runAction(target: target) {
            let config = try await self.scraperConfigService.subscribeFeed(
                feedURL: result.feedURL,
                feedType: result.feedType,
                displayName: result.title
            )
            return config.subscriptionOutcome == .already_subscribed
                ? "Already subscribed"
                : "Subscribed"
        }
    }

    func addPodcastEpisode(_ result: PodcastSearchResult) async {
        let target = SearchActionTarget.episode(result.id)
        guard let url = URL(string: result.episodeURL) else {
            actionErrorMessages[target] = "This episode link is invalid."
            return
        }
        await runAction(target: target) {
            _ = try await self.contentService.submitContent(
                url: url,
                title: result.title,
                platform: nil
            )
            return "Added"
        }
    }

    func subscribeToPodcast(_ result: PodcastSearchResult) async {
        guard let feedURL = result.feedURL else { return }
        let target = SearchActionTarget.podcastFeed(feedURL)
        await runAction(target: target) {
            let config = try await self.scraperConfigService.subscribeFeed(
                feedURL: feedURL,
                feedType: "podcast_rss",
                displayName: result.podcastTitle ?? result.title
            )
            return config.subscriptionOutcome == .already_subscribed
                ? "Already subscribed"
                : "Subscribed"
        }
    }

    func searchTextDidChange(to searchText: String) {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard query != observedQuery else { return }
        observedQuery = query

        contentResults = []
        hasLocalSearch = false
        localErrorMessage = nil

        tasks.cancel(.mixed)
        isLoadingMixed = false
        hasSubmittedSearch = false
        feedResults = []
        podcastResults = []
        completedActionLabels = [:]
        actionErrorMessages = [:]
        mixedErrorMessage = nil

        guard query.count >= 2 else {
            tasks.cancel(.local)
            isLoadingLocal = false
            return
        }

        startLocalSearch(for: query, debounce: true)
    }

    private func startLocalSearch(for query: String, debounce: Bool) {
        isLoadingLocal = true
        localErrorMessage = nil
        tasks.runReplacing(.local) { [weak self] token in
            await self?.runLocalSearch(for: query, debounce: debounce, token: token)
        }
    }

    private func runLocalSearch(
        for query: String,
        debounce: Bool,
        token: TaskBag<TaskKey>.Token
    ) async {
        defer {
            if tasks.isCurrent(token) {
                isLoadingLocal = false
            }
        }

        if debounce {
            do {
                try await Task.sleep(for: .milliseconds(350))
            } catch {
                return
            }
        }

        guard tasks.isCurrent(token), trimmedQuery == query else { return }

        do {
            let response = try await contentService.searchContent(
                query: query,
                contentType: "all",
                limit: localSearchResultLimit,
                cursor: nil
            )
            guard tasks.isCurrent(token), trimmedQuery == query else { return }
            contentResults = response.contents
            hasLocalSearch = true
        } catch {
            guard tasks.isCurrent(token), trimmedQuery == query else { return }
            localErrorMessage = "Newsbuddy couldn't search your content."
            contentResults = []
            hasLocalSearch = true
        }
    }

    private func runMixedSearch(for query: String, token: TaskBag<TaskKey>.Token) async {
        isLoadingMixed = true
        mixedErrorMessage = nil
        defer {
            if tasks.isCurrent(token) {
                isLoadingMixed = false
            }
        }

        do {
            let response = try await contentService.searchMixed(query: query, limit: 10)
            guard !Task.isCancelled,
                  tasks.isCurrent(token),
                  trimmedQuery == query else { return }
            contentResults = response.content
            feedResults = response.feeds
            podcastResults = response.podcasts
            for result in response.feeds where result.isSubscribed {
                completedActionLabels[.feed(result.id)] = "Subscribed"
            }
            hasLocalSearch = true
            hasSubmittedSearch = true
        } catch {
            guard !Task.isCancelled,
                  tasks.isCurrent(token),
                  trimmedQuery == query else { return }
            mixedErrorMessage = "Newsbuddy couldn't search external sources."
            feedResults = []
            podcastResults = []
            hasSubmittedSearch = true
        }
    }

    private func runAction(
        target: SearchActionTarget,
        action: @escaping () async throws -> String
    ) async {
        guard completedActionLabels[target] == nil,
              !actionTargetsInFlight.contains(target) else { return }
        actionTargetsInFlight.insert(target)
        actionErrorMessages[target] = nil
        defer { actionTargetsInFlight.remove(target) }

        do {
            completedActionLabels[target] = try await action()
        } catch {
            actionErrorMessages[target] = "That action didn't finish. Please try again."
        }
    }
}
