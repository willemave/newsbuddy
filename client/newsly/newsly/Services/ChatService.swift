//
//  ChatService.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation

/// Errors specific to the chat service
enum ChatServiceError: LocalizedError {
    case missingAssistantMessage
    case processingFailed(String)
    case timeout

    var errorDescription: String? {
        switch self {
        case .missingAssistantMessage:
            return "Assistant response was missing from completed message"
        case .processingFailed(let error):
            return "Message processing failed: \(error)"
        case .timeout:
            return "Message processing timed out"
        }
    }
}

protocol ChatSessionServicing: MessageStatusFetching {
    func getSession(id: Int) async throws -> ChatSessionDetail
    func sendMessageAsync(sessionId: Int, message: String) async throws -> SendChatMessageResponse
    func startCouncil(sessionId: Int, message: String) async throws -> ChatSessionDetail
    func selectCouncilBranch(sessionId: Int, childSessionId: Int) async throws -> ChatSessionDetail
    func retryCouncilBranch(sessionId: Int, childSessionId: Int) async throws -> ChatSessionDetail
    func updateSessionProvider(sessionId: Int, provider: ChatModelProvider) async throws -> ChatSessionSummary
}

extension ChatService: ChatSessionServicing {}

class ChatService {
    static let shared = ChatService()
    private let client = APIClient.shared

    private init() {}

    // MARK: - Session Management

    private func sessionListQueryItems(
        contentId: Int?,
        newsItemId: Int?,
        limit: Int,
        cursor: String? = nil
    ) -> [URLQueryItem] {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit))
        ]
        if let contentId {
            queryItems.append(URLQueryItem(name: "content_id", value: String(contentId)))
        }
        if let newsItemId {
            queryItems.append(URLQueryItem(name: "news_item_id", value: String(newsItemId)))
        }
        if let cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }
        return queryItems
    }

    /// List all chat sessions for the current user
    func listSessions(
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        limit: Int = 50
    ) async throws -> [ChatSessionSummary] {
        let response: [APIChatSessionSummary] = try await client.request(
            APIEndpoints.chatSessions,
            queryItems: sessionListQueryItems(
                contentId: contentId,
                newsItemId: newsItemId,
                limit: limit
            ),
            recoveryPolicy: .safeRead
        )
        return response.map(ChatSessionSummary.init(api:))
    }

    /// List one page of chat sessions for the current user
    func listSessionsPage(
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        limit: Int = 25,
        cursor: String? = nil
    ) async throws -> ChatSessionListResponse {
        let response: APIChatSessionListResponse = try await client.request(
            APIEndpoints.chatSessionsList,
            queryItems: sessionListQueryItems(
                contentId: contentId,
                newsItemId: newsItemId,
                limit: limit,
                cursor: cursor
            ),
            headers: ["Cache-Control": "no-cache"],
            recoveryPolicy: .safeRead
        )
        return ChatSessionListResponse(api: response)
    }

    /// Create a new chat session
    func createSession(
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        topic: String? = nil,
        provider: ChatModelProvider? = .openai,
        modelHint: String? = nil,
        initialMessage: String? = nil
    ) async throws -> ChatSessionSummary {
        let request = APICreateChatSessionRequest(
            contentId: contentId,
            newsItemId: newsItemId,
            topic: topic,
            llmProvider: provider.flatMap { APILLMProvider(rawValue: $0.rawValue) },
            llmModelHint: modelHint,
            initialMessage: initialMessage
        )

        let encoder = JSONEncoder()
        let body = try encoder.encode(request)

        let response: APICreateChatSessionResponse = try await client.request(
            APIEndpoints.chatSessions,
            method: .post,
            body: body
        )

        return ChatSessionSummary(api: response.session)
    }

    /// Get session details with message history
    func getSession(id: Int) async throws -> ChatSessionDetail {
        let response: APIChatSessionDetail = try await client.request(
            APIEndpoints.chatSession(id: id),
            recoveryPolicy: .safeRead
        )
        return ChatSessionDetail(api: response)
    }

    /// Check if a session exists for the given content
    func getSessionForContent(contentId: Int) async throws -> ChatSessionSummary? {
        let sessions = try await listSessions(contentId: contentId, limit: 20)
        return sessions.first(where: {
            $0.contentId == contentId &&
            $0.isKnowledgeSession &&
            !($0.councilMode ?? false)
        })
    }

    func getSessionForNewsItem(newsItemId: Int) async throws -> ChatSessionSummary? {
        let sessions = try await listSessions(newsItemId: newsItemId, limit: 20)
        return sessions.first(where: {
            $0.newsItemId == newsItemId &&
            $0.isKnowledgeSession &&
            !($0.councilMode ?? false)
        })
    }

    /// Update a session's provider (allows switching models mid-conversation)
    func updateSessionProvider(
        sessionId: Int,
        provider: ChatModelProvider
    ) async throws -> ChatSessionSummary {
        let request = APIUpdateChatSessionRequest(
            llmProvider: APILLMProvider(rawValue: provider.rawValue),
            llmModelHint: nil
        )

        let encoder = JSONEncoder()
        let body = try encoder.encode(request)

        let response: APIChatSessionSummary = try await client.request(
            APIEndpoints.chatSession(id: sessionId),
            method: .patch,
            body: body
        )
        return ChatSessionSummary(api: response)
    }

    /// Soft-delete (archive) a chat session
    func deleteSession(sessionId: Int) async throws {
        try await client.requestVoid(
            APIEndpoints.chatSession(id: sessionId),
            method: .delete
        )
    }

    // MARK: - Messaging

    /// Send a message and start async processing
    /// Returns immediately with the pending message info
    func sendMessageAsync(
        sessionId: Int,
        message: String
    ) async throws -> SendChatMessageResponse {
        let request = APISendChatMessageRequest(message: message)
        let encoder = JSONEncoder()
        let body = try encoder.encode(request)

        let response: APISendMessageResponse = try await client.request(
            APIEndpoints.chatMessages(sessionId: sessionId),
            method: .post,
            body: body
        )
        return SendChatMessageResponse(api: response)
    }

    /// Create or continue a contextual assistant turn.
    func createAssistantTurn(
        message: String,
        sessionId: Int? = nil,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse {
        let request = APIAssistantTurnRequest(
            message: message,
            sessionId: sessionId,
            screenContext: screenContext.api
        )
        let body = try JSONEncoder().encode(request)
        let response: APIAssistantTurnResponse = try await client.request(
            APIEndpoints.assistantTurns,
            method: .post,
            body: body
        )
        return AssistantTurnResponse(api: response)
    }

    /// Poll for message status
    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse {
        let response: APIMessageStatusResponse = try await client.request(
            APIEndpoints.chatMessageStatus(messageId: messageId)
        )
        return MessageStatusResponse(api: response)
    }

    /// Start council mode for an existing session and return the merged parent transcript.
    func startCouncil(
        sessionId: Int,
        message: String
    ) async throws -> ChatSessionDetail {
        let request = APICouncilStartRequest(message: message)
        let body = try JSONEncoder().encode(request)
        let response: APIChatSessionDetail = try await client.request(
            APIEndpoints.chatCouncilStart(sessionId: sessionId),
            method: .post,
            body: body
        )
        return ChatSessionDetail(api: response)
    }

    /// Select the active council branch and return the merged parent transcript.
    func selectCouncilBranch(
        sessionId: Int,
        childSessionId: Int
    ) async throws -> ChatSessionDetail {
        let request = APICouncilSelectRequest(childSessionId: childSessionId)
        let body = try JSONEncoder().encode(request)
        let response: APIChatSessionDetail = try await client.request(
            APIEndpoints.chatCouncilSelect(sessionId: sessionId),
            method: .post,
            body: body
        )
        return ChatSessionDetail(api: response)
    }

    /// Retry one council branch and return the merged parent transcript.
    func retryCouncilBranch(
        sessionId: Int,
        childSessionId: Int
    ) async throws -> ChatSessionDetail {
        let request = APICouncilRetryRequest(childSessionId: childSessionId)
        let body = try JSONEncoder().encode(request)
        let response: APIChatSessionDetail = try await client.request(
            APIEndpoints.chatCouncilRetry(sessionId: sessionId),
            method: .post,
            body: body
        )
        return ChatSessionDetail(api: response)
    }

    // MARK: - Convenience Methods

    /// Start a deep dive chat for an article
    func startArticleChat(
        contentId: Int,
        provider: ChatModelProvider = .openai
    ) async throws -> ChatSessionSummary {
        // Source-launched article chats should always create a fresh thread so
        // the conversation state and selected provider stay scoped to this run.
        return try await createSession(
            contentId: contentId,
            provider: provider
        )
    }

    func startNewsChat(
        newsItemId: Int,
        provider: ChatModelProvider = .openai
    ) async throws -> ChatSessionSummary {
        return try await createSession(
            newsItemId: newsItemId,
            provider: provider
        )
    }

    /// Start a topic-focused chat for an article
    func startTopicChat(
        contentId: Int,
        topic: String,
        provider: ChatModelProvider = .openai
    ) async throws -> ChatSessionSummary {
        return try await createSession(
            contentId: contentId,
            topic: topic,
            provider: provider
        )
    }

    func startNewsTopicChat(
        newsItemId: Int,
        topic: String,
        provider: ChatModelProvider = .openai
    ) async throws -> ChatSessionSummary {
        return try await createSession(
            newsItemId: newsItemId,
            topic: topic,
            provider: provider
        )
    }

}
