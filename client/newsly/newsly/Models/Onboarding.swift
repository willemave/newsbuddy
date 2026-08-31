//
//  Onboarding.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import Foundation

struct OnboardingAudioDiscoverRequest: Codable {
    let transcript: String
    let locale: String?

    var api: APIOnboardingAudioDiscoverRequest {
        APIOnboardingAudioDiscoverRequest(transcript: transcript, locale: locale)
    }
}

struct OnboardingDiscoveryLaneStatus: Codable, Hashable, Identifiable {
    let name: String
    let status: String
    let completedQueries: Int
    let queryCount: Int

    var id: String { name }

    init(name: String, status: String, completedQueries: Int, queryCount: Int) {
        self.name = name
        self.status = status
        self.completedQueries = completedQueries
        self.queryCount = queryCount
    }

    init(api response: APIOnboardingDiscoveryLaneStatus) {
        name = response.name
        status = response.status
        completedQueries = response.completedQueries
        queryCount = response.queryCount
    }

    enum CodingKeys: String, CodingKey {
        case name
        case status
        case completedQueries = "completed_queries"
        case queryCount = "query_count"
    }
}

struct OnboardingAudioDiscoverResponse: Codable {
    let runId: Int
    let runStatus: String
    let topicSummary: String?
    let inferredTopics: [String]
    let lanes: [OnboardingDiscoveryLaneStatus]

    init(
        runId: Int,
        runStatus: String,
        topicSummary: String?,
        inferredTopics: [String],
        lanes: [OnboardingDiscoveryLaneStatus]
    ) {
        self.runId = runId
        self.runStatus = runStatus
        self.topicSummary = topicSummary
        self.inferredTopics = inferredTopics
        self.lanes = lanes
    }

    init(api response: APIOnboardingAudioDiscoverResponse) {
        runId = response.runId
        runStatus = response.runStatus
        topicSummary = response.topicSummary
        inferredTopics = response.inferredTopics
        lanes = response.lanes.map(OnboardingDiscoveryLaneStatus.init(api:))
    }

    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case runStatus = "run_status"
        case topicSummary = "topic_summary"
        case inferredTopics = "inferred_topics"
        case lanes
    }
}

struct OnboardingSuggestion: Codable, Hashable {
    let id: Int?
    let suggestionType: String
    let title: String?
    let siteURL: String?
    let feedURL: String?
    let subreddit: String?
    let rationale: String?
    let score: Double?
    let isDefault: Bool

    init(
        id: Int?,
        suggestionType: String,
        title: String?,
        siteURL: String?,
        feedURL: String?,
        subreddit: String?,
        rationale: String?,
        score: Double?,
        isDefault: Bool
    ) {
        self.id = id
        self.suggestionType = suggestionType
        self.title = title
        self.siteURL = siteURL
        self.feedURL = feedURL
        self.subreddit = subreddit
        self.rationale = rationale
        self.score = score
        self.isDefault = isDefault
    }

    init(api response: APIOnboardingSuggestion) {
        id = response.id
        suggestionType = response.suggestionType.rawValue
        title = response.title
        siteURL = response.siteUrl
        feedURL = response.feedUrl
        subreddit = response.subreddit
        rationale = response.rationale
        score = response.score
        isDefault = response.isDefault
    }

    enum CodingKeys: String, CodingKey {
        case id
        case suggestionType = "suggestion_type"
        case title
        case siteURL = "site_url"
        case feedURL = "feed_url"
        case subreddit
        case rationale
        case score
        case isDefault = "is_default"
    }

    var stableKey: String {
        id.map(String.init) ?? feedURL ?? subreddit ?? siteURL ?? title ?? UUID().uuidString
    }

    var displayTitle: String {
        if suggestionType == "reddit",
           let redditLabel = Self.formatRedditLabel(subreddit ?? title ?? siteURL)
        {
            return redditLabel
        }

        if let title, !title.isEmpty {
            return title
        }
        if let subreddit, !subreddit.isEmpty {
            return "r/\(subreddit)"
        }
        return feedURL ?? "Untitled"
    }

    private static func formatRedditLabel(_ rawValue: String?) -> String? {
        guard var value = rawValue?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
            return nil
        }

        if let url = URL(string: value),
           let host = url.host?.lowercased(),
           host.contains("reddit.com")
        {
            let pathParts = url.path
                .split(separator: "/", omittingEmptySubsequences: true)
                .map(String.init)
            if let rIndex = pathParts.firstIndex(where: { $0.lowercased() == "r" }),
               rIndex + 1 < pathParts.count
            {
                value = pathParts[rIndex + 1]
            }
        }

        value = value.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if value.lowercased().hasPrefix("r/") {
            value = String(value.dropFirst(2))
        }
        if let queryIndex = value.firstIndex(of: "?") {
            value = String(value[..<queryIndex])
        }
        value = value.trimmingCharacters(in: CharacterSet(charactersIn: "/"))

        guard !value.isEmpty else { return nil }
        return "r/\(value)"
    }
}

struct OnboardingFastDiscoverResponse: Codable {
    let recommendedPods: [OnboardingSuggestion]
    let recommendedSubstacks: [OnboardingSuggestion]
    let recommendedSubreddits: [OnboardingSuggestion]

    init(
        recommendedPods: [OnboardingSuggestion],
        recommendedSubstacks: [OnboardingSuggestion],
        recommendedSubreddits: [OnboardingSuggestion]
    ) {
        self.recommendedPods = recommendedPods
        self.recommendedSubstacks = recommendedSubstacks
        self.recommendedSubreddits = recommendedSubreddits
    }

    init(api response: APIOnboardingFastDiscoverResponse) {
        recommendedPods = response.recommendedPods.map(OnboardingSuggestion.init(api:))
        recommendedSubstacks = response.recommendedSubstacks.map(OnboardingSuggestion.init(api:))
        recommendedSubreddits = response.recommendedSubreddits.map(OnboardingSuggestion.init(api:))
    }

    enum CodingKeys: String, CodingKey {
        case recommendedPods = "recommended_pods"
        case recommendedSubstacks = "recommended_substacks"
        case recommendedSubreddits = "recommended_subreddits"
    }
}

struct OnboardingDiscoveryStatusResponse: Codable {
    let runId: Int
    let runStatus: String
    let topicSummary: String?
    let inferredTopics: [String]
    let lanes: [OnboardingDiscoveryLaneStatus]
    let suggestions: OnboardingFastDiscoverResponse?
    let errorMessage: String?

    init(
        runId: Int,
        runStatus: String,
        topicSummary: String?,
        inferredTopics: [String],
        lanes: [OnboardingDiscoveryLaneStatus],
        suggestions: OnboardingFastDiscoverResponse?,
        errorMessage: String?
    ) {
        self.runId = runId
        self.runStatus = runStatus
        self.topicSummary = topicSummary
        self.inferredTopics = inferredTopics
        self.lanes = lanes
        self.suggestions = suggestions
        self.errorMessage = errorMessage
    }

    init(api response: APIOnboardingDiscoveryStatusResponse) {
        runId = response.runId
        runStatus = response.runStatus
        topicSummary = response.topicSummary
        inferredTopics = response.inferredTopics
        lanes = response.lanes.map(OnboardingDiscoveryLaneStatus.init(api:))
        suggestions = response.suggestions.map(OnboardingFastDiscoverResponse.init(api:))
        errorMessage = response.errorMessage
    }

    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case runStatus = "run_status"
        case topicSummary = "topic_summary"
        case inferredTopics = "inferred_topics"
        case lanes
        case suggestions
        case errorMessage = "error_message"
    }
}

struct OnboardingSelectedAggregator: Codable, Hashable {
    let key: String
    let title: String?
    let topics: [String]

    init(key: String, title: String? = nil, topics: [String] = []) {
        self.key = key
        self.title = title
        self.topics = topics
    }

    var api: APIOnboardingSelectedAggregator {
        APIOnboardingSelectedAggregator(key: key, title: title, topics: topics)
    }
}

struct OnboardingCompleteRequest: Codable {
    let discoveryRunId: Int?
    let selectedSuggestionIds: [Int]
    let selectedAggregators: [OnboardingSelectedAggregator]
    let twitterUsername: String?

    init(
        discoveryRunId: Int?,
        selectedSuggestionIds: [Int],
        selectedAggregators: [OnboardingSelectedAggregator] = [],
        twitterUsername: String?
    ) {
        self.discoveryRunId = discoveryRunId
        self.selectedSuggestionIds = selectedSuggestionIds
        self.selectedAggregators = selectedAggregators
        self.twitterUsername = twitterUsername
    }

    var api: APIOnboardingCompleteRequest {
        APIOnboardingCompleteRequest(
            discoveryRunId: discoveryRunId,
            selectedSuggestionIds: selectedSuggestionIds,
            selectedAggregators: selectedAggregators.map(\.api),
            twitterUsername: twitterUsername
        )
    }

    enum CodingKeys: String, CodingKey {
        case discoveryRunId = "discovery_run_id"
        case selectedSuggestionIds = "selected_suggestion_ids"
        case selectedAggregators = "selected_aggregators"
        case twitterUsername = "twitter_username"
    }
}

struct OnboardingCompleteResponse: Codable, Equatable {
    let status: String
    let taskId: Int?
    let inboxCountEstimate: Int
    let configuredSourceCount: Int
    let longformStatus: String
    let hasCompletedOnboarding: Bool
    let hasCompletedNewUserTutorial: Bool

    init(
        status: String,
        taskId: Int?,
        inboxCountEstimate: Int,
        configuredSourceCount: Int,
        longformStatus: String,
        hasCompletedOnboarding: Bool,
        hasCompletedNewUserTutorial: Bool
    ) {
        self.status = status
        self.taskId = taskId
        self.inboxCountEstimate = inboxCountEstimate
        self.configuredSourceCount = configuredSourceCount
        self.longformStatus = longformStatus
        self.hasCompletedOnboarding = hasCompletedOnboarding
        self.hasCompletedNewUserTutorial = hasCompletedNewUserTutorial
    }

    init(api response: APIOnboardingCompleteResponse) {
        status = response.status
        taskId = response.taskId
        inboxCountEstimate = response.inboxCountEstimate
        configuredSourceCount = response.configuredSourceCount
        longformStatus = response.longformStatus
        hasCompletedOnboarding = response.hasCompletedOnboarding
        hasCompletedNewUserTutorial = response.hasCompletedNewUserTutorial
    }

    enum CodingKeys: String, CodingKey {
        case status
        case taskId = "task_id"
        case inboxCountEstimate = "inbox_count_estimate"
        case configuredSourceCount = "configured_source_count"
        case longformStatus = "longform_status"
        case hasCompletedOnboarding = "has_completed_onboarding"
        case hasCompletedNewUserTutorial = "has_completed_new_user_tutorial"
    }
}

struct OnboardingTutorialResponse: Codable {
    let hasCompletedNewUserTutorial: Bool

    init(hasCompletedNewUserTutorial: Bool) {
        self.hasCompletedNewUserTutorial = hasCompletedNewUserTutorial
    }

    init(api response: APIOnboardingTutorialResponse) {
        hasCompletedNewUserTutorial = response.hasCompletedNewUserTutorial
    }

    enum CodingKeys: String, CodingKey {
        case hasCompletedNewUserTutorial = "has_completed_new_user_tutorial"
    }
}
