//
//  APIEndpoints.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

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
    static let fastNewsAudioEpisode = "/api/content/audio-episodes/fast-news"
    static func contentCouncilAudioEpisode(id: Int) -> String {
        return "/api/content/\(id)/audio-episodes/council"
    }
    static let customNarrationAudioEpisodes = "/api/content/audio-episodes/custom-narrations"
    static func audioEpisode(id: Int) -> String {
        return "/api/content/audio-episodes/\(id)"
    }
    static func audioEpisodeAudio(id: Int) -> String {
        return "/api/content/audio-episodes/\(id)/audio"
    }
    static func audioEpisodeShare(id: Int) -> String {
        return "/api/content/audio-episodes/\(id)/share"
    }
    static func newsItem(id: Int) -> String {
        return "/api/news/items/\(id)"
    }
    static func newsItemBody(id: Int) -> String {
        return "/api/news/items/\(id)/body"
    }
    static func newsItemDiscussion(id: Int) -> String {
        return "/api/news/items/\(id)/discussion"
    }
    static func newsItemAudioEpisode(id: Int) -> String {
        return "/api/news/items/\(id)/audio-episodes/discussion"
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
    static let badgeStats = "/api/content/stats/badge"
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
    static let feedback = "/api/feedback"

    // MARK: - Briefing Endpoints
    static let briefing = "/api/briefing"
    static func briefingLens(_ key: String) -> String {
        return "/api/briefing/lenses/\(key)"
    }
    static let briefingReadMarks = "/api/briefing/read-marks"
    static let briefingRefresh = "/api/briefing/refresh"
    static let briefingDigSearch = "/api/briefing/dig/search"
    static let briefingDigSummarize = "/api/briefing/dig/summarize"
    static let briefingNarration = "/api/briefing/narration"

    // MARK: - Onboarding Endpoints
    static let onboardingComplete = "/api/onboarding/complete"
    static let onboardingTutorialComplete = "/api/onboarding/tutorial-complete"
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
    static let chatSessionsList = "/api/content/chat/sessions/list"
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

    // MARK: - Learning Deck Endpoints
    static let learningDecks = "/api/learning/decks"
    static func learningDeck(id: Int) -> String {
        return "/api/learning/decks/\(id)"
    }
    static func learningDeckViewerURL(id: Int) -> String {
        return "/api/learning/decks/\(id)/viewer-url"
    }
    static func learningDeckSourceNotesURL(id: Int) -> String {
        return "/api/learning/decks/\(id)/source-notes-url"
    }
    static func learningDeckShare(id: Int) -> String {
        return "/api/learning/decks/\(id)/share"
    }
}
