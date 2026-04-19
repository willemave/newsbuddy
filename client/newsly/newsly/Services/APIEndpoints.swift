//
//  APIEndpoints.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Combine
import Foundation

enum APIEndpoints {
    static let contentList = "/api/content/"
    static let newsItems = "/api/news/items"
    static let newsItemsMarkRead = "/api/news/items/mark-read"
    static let submitContent = "/api/content/submit"
    static let submissionStatusList = "/api/content/submissions/list"
    static let searchContent = "/api/content/search"
    static let searchMixedContent = "/api/content/search/mixed"
    static func contentDetail(id: Int) -> String {
        return "/api/content/\(id)"
    }
    static func contentBody(id: Int) -> String {
        return "/api/content/\(id)/body"
    }
    static func markContentRead(id: Int) -> String {
        return "/api/content/\(id)/mark-read"
    }
    static func markContentUnread(id: Int) -> String {
        return "/api/content/\(id)/mark-unread"
    }
    static func narration(_ target: NarrationTarget) -> String {
        return "/api/content/narration/\(target.pathComponent)/\(target.id)"
    }
    static func newsItem(id: Int) -> String {
        return "/api/news/items/\(id)"
    }
    static func newsItemDiscussion(id: Int) -> String {
        return "/api/news/items/\(id)/discussion"
    }
    static func newsItemDiscussionRefresh(id: Int) -> String {
        return "/api/news/items/\(id)/discussion/refresh"
    }
    static let analytics = "/api/analytics"
    static let bulkMarkRead = "/api/content/bulk-mark-read"
    static func saveToKnowledge(id: Int) -> String {
        return "/api/content/\(id)/knowledge"
    }
    static func removeFromKnowledge(id: Int) -> String {
        return "/api/content/\(id)/knowledge"
    }
    static let knowledgeLibraryList = "/api/content/knowledge/list"
    static let recentlyReadList = "/api/content/recently-read/list"
    static func chatGPTUrl(id: Int) -> String {
        return "/api/content/\(id)/chat-url"
    }
    static func contentDiscussion(id: Int) -> String {
        return "/api/content/\(id)/discussion"
    }
    static func contentDiscussionRefresh(id: Int) -> String {
        return "/api/content/\(id)/discussion/refresh"
    }
    static let unreadCounts = "/api/content/stats/unread-counts"
    static let processingCount = "/api/content/stats/processing-count"
    static func convertNewsToArticle(id: Int) -> String {
        return "/api/content/\(id)/convert-to-article"
    }
    static func convertNewsItemToArticle(id: Int) -> String {
        return "/api/news/items/\(id)/convert-to-article"
    }
    static func downloadMoreFromSeries(id: Int) -> String {
        return "/api/content/\(id)/download-more"
    }
    static let scraperConfigs = "/api/scrapers/"
    static func scraperConfig(id: Int) -> String {
        return "/api/scrapers/\(id)"
    }
    static let subscribeFeed = "/api/scrapers/subscribe"
    static func tweetSuggestions(id: Int) -> String {
        return "/api/content/\(id)/tweet-suggestions"
    }

    // MARK: - Auth Endpoints
    static let authDebugNewUser = "/auth/debug/new-user"
    static let authMe = "/auth/me"
    static func cliLinkApprove(sessionID: String) -> String {
        return "/api/agent/cli/link/\(sessionID)/approve"
    }

    // MARK: - Discovery Endpoints
    static let discoverySuggestions = "/api/discovery/suggestions"
    static let discoveryHistory = "/api/discovery/history"
    static let discoveryRefresh = "/api/discovery/refresh"
    static let discoveryPodcastSearch = "/api/discovery/search/podcasts"
    static let discoverySubscribe = "/api/discovery/subscribe"
    static let discoveryAddItem = "/api/discovery/add-item"
    static let discoveryDismiss = "/api/discovery/dismiss"
    static let discoveryClear = "/api/discovery/clear"

    // MARK: - Onboarding Endpoints
    static let onboardingProfile = "/api/onboarding/profile"
    static let onboardingFastDiscover = "/api/onboarding/fast-discover"
    static let onboardingComplete = "/api/onboarding/complete"
    static let onboardingTutorialComplete = "/api/onboarding/tutorial-complete"
    static let onboardingParseVoice = "/api/onboarding/parse-voice"
    static let onboardingAudioDiscover = "/api/onboarding/audio-discover"
    static let onboardingDiscoveryStatus = "/api/onboarding/discovery-status"

    // MARK: - Integrations
    static let xIntegrationConnection = "/api/integrations/x/connection"
    static let xIntegrationOAuthStart = "/api/integrations/x/oauth/start"
    static let xIntegrationOAuthExchange = "/api/integrations/x/oauth/exchange"

    // MARK: - OpenAI Endpoints
    static let openaiTranscriptions = "/api/openai/transcriptions"

    // MARK: - Chat Endpoints
    static let chatSessions = "/api/content/chat/sessions"
    static func chatSession(id: Int) -> String {
        return "/api/content/chat/sessions/\(id)"
    }
    static func chatMessages(sessionId: Int) -> String {
        return "/api/content/chat/sessions/\(sessionId)/messages"
    }
    static func chatInitialSuggestions(sessionId: Int) -> String {
        return "/api/content/chat/sessions/\(sessionId)/initial-suggestions"
    }
    static func chatCouncilStart(sessionId: Int) -> String {
        return "/api/content/chat/sessions/\(sessionId)/council/start"
    }
    static func chatCouncilSelect(sessionId: Int) -> String {
        return "/api/content/chat/sessions/\(sessionId)/council/select"
    }
    static func chatCouncilRetry(sessionId: Int) -> String {
        return "/api/content/chat/sessions/\(sessionId)/council/retry"
    }
    static func chatMessageStatus(messageId: Int) -> String {
        return "/api/content/chat/messages/\(messageId)/status"
    }
    static let assistantTurns = "/api/content/chat/assistant/turns"
}
