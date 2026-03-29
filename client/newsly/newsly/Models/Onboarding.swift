//
//  Onboarding.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import Foundation

struct OnboardingProfileRequest: Codable {
    let firstName: String
    let interestTopics: [String]

    enum CodingKeys: String, CodingKey {
        case firstName = "first_name"
        case interestTopics = "interest_topics"
    }
}

struct OnboardingProfileResponse: Codable {
    let profileSummary: String
    let inferredTopics: [String]
    let candidateSources: [String]

    enum CodingKeys: String, CodingKey {
        case profileSummary = "profile_summary"
        case inferredTopics = "inferred_topics"
        case candidateSources = "candidate_sources"
    }
}

struct OnboardingVoiceParseRequest: Codable {
    let transcript: String
    let locale: String?
}

struct OnboardingVoiceParseResponse: Codable {
    let firstName: String?
    let interestTopics: [String]
    let confidence: Double?
    let missingFields: [String]

    enum CodingKeys: String, CodingKey {
        case firstName = "first_name"
        case interestTopics = "interest_topics"
        case confidence
        case missingFields = "missing_fields"
    }
}

struct OnboardingAudioDiscoverRequest: Codable {
    let transcript: String
    let locale: String?
}

struct OnboardingDiscoveryLaneStatus: Codable, Hashable, Identifiable {
    let name: String
    let status: String
    let completedQueries: Int
    let queryCount: Int

    var id: String { name }

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

    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case runStatus = "run_status"
        case topicSummary = "topic_summary"
        case inferredTopics = "inferred_topics"
        case lanes
    }
}

struct OnboardingSuggestion: Codable, Hashable {
    let suggestionType: String
    let title: String?
    let siteURL: String?
    let feedURL: String?
    let subreddit: String?
    let rationale: String?
    let score: Double?
    let isDefault: Bool

    enum CodingKeys: String, CodingKey {
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
        feedURL ?? subreddit ?? siteURL ?? title ?? UUID().uuidString
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

struct OnboardingFastDiscoverRequest: Codable {
    let profileSummary: String
    let inferredTopics: [String]

    enum CodingKeys: String, CodingKey {
        case profileSummary = "profile_summary"
        case inferredTopics = "inferred_topics"
    }
}

struct OnboardingFastDiscoverResponse: Codable {
    let recommendedPods: [OnboardingSuggestion]
    let recommendedSubstacks: [OnboardingSuggestion]
    let recommendedSubreddits: [OnboardingSuggestion]

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

struct OnboardingSelectedSource: Codable {
    let suggestionType: String
    let title: String?
    let feedURL: String
    let config: [String: String]?

    enum CodingKeys: String, CodingKey {
        case suggestionType = "suggestion_type"
        case title
        case feedURL = "feed_url"
        case config
    }
}

struct OnboardingCompleteRequest: Codable {
    let selectedSources: [OnboardingSelectedSource]
    let selectedSubreddits: [String]
    let profileSummary: String?
    let inferredTopics: [String]?
    let twitterUsername: String?
    let newsDigestPreferencePrompt: String?

    enum CodingKeys: String, CodingKey {
        case selectedSources = "selected_sources"
        case selectedSubreddits = "selected_subreddits"
        case profileSummary = "profile_summary"
        case inferredTopics = "inferred_topics"
        case twitterUsername = "twitter_username"
        case newsDigestPreferencePrompt = "news_digest_preference_prompt"
    }
}

struct OnboardingCompleteResponse: Codable, Equatable {
    let status: String
    let taskId: Int?
    let inboxCountEstimate: Int
    let longformStatus: String
    let hasCompletedOnboarding: Bool
    let hasCompletedNewUserTutorial: Bool

    enum CodingKeys: String, CodingKey {
        case status
        case taskId = "task_id"
        case inboxCountEstimate = "inbox_count_estimate"
        case longformStatus = "longform_status"
        case hasCompletedOnboarding = "has_completed_onboarding"
        case hasCompletedNewUserTutorial = "has_completed_new_user_tutorial"
    }
}

struct OnboardingTutorialResponse: Codable {
    let hasCompletedNewUserTutorial: Bool

    enum CodingKeys: String, CodingKey {
        case hasCompletedNewUserTutorial = "has_completed_new_user_tutorial"
    }
}
