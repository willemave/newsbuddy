//
//  ChatService.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Combine
import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ChatService")

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

/// Request to update a chat session
struct UpdateChatSessionRequest: Codable {
    var llmProvider: String?
    var llmModelHint: String?

    enum CodingKeys: String, CodingKey {
        case llmProvider = "llm_provider"
        case llmModelHint = "llm_model_hint"
    }
}

class ChatService {
    static let shared = ChatService()
    private let client = APIClient.shared

    /// Polling interval for checking message status (500ms)
    private let pollingInterval: UInt64 = 500_000_000 // nanoseconds

    /// Maximum polling attempts before timeout (60 seconds at 500ms intervals = 120 attempts)
    private let maxPollingAttempts = 120

    private init() {}

    // MARK: - Session Management

    /// List all chat sessions for the current user
    func listSessions(
        contentId: Int? = nil,
        limit: Int = 50
    ) async throws -> [ChatSessionSummary] {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit))
        ]

        if let contentId = contentId {
            queryItems.append(URLQueryItem(name: "content_id", value: String(contentId)))
        }

        return try await client.request(
            APIEndpoints.chatSessions,
            queryItems: queryItems
        )
    }

    /// Create a new chat session
    func createSession(
        contentId: Int? = nil,
        topic: String? = nil,
        provider: ChatModelProvider? = .anthropic,
        modelHint: String? = nil,
        initialMessage: String? = nil
    ) async throws -> ChatSessionSummary {
        let request = CreateChatSessionRequest(
            contentId: contentId,
            topic: topic,
            llmProvider: provider?.rawValue,
            llmModelHint: modelHint,
            initialMessage: initialMessage
        )

        let encoder = JSONEncoder()
        let body = try encoder.encode(request)

        let response: CreateChatSessionResponse = try await client.request(
            APIEndpoints.chatSessions,
            method: "POST",
            body: body
        )

        return response.session
    }

    /// Get session details with message history
    func getSession(id: Int) async throws -> ChatSessionDetail {
        return try await client.request(APIEndpoints.chatSession(id: id))
    }

    /// Check if a session exists for the given content
    func getSessionForContent(contentId: Int) async throws -> ChatSessionSummary? {
        let sessions = try await listSessions(contentId: contentId, limit: 20)
        return sessions.first(where: { $0.isKnowledgeSession }) ?? sessions.first
    }

    /// Update a session's provider (allows switching models mid-conversation)
    func updateSessionProvider(
        sessionId: Int,
        provider: ChatModelProvider
    ) async throws -> ChatSessionSummary {
        let request = UpdateChatSessionRequest(
            llmProvider: provider.rawValue,
            llmModelHint: nil
        )

        let encoder = JSONEncoder()
        let body = try encoder.encode(request)

        return try await client.request(
            APIEndpoints.chatSession(id: sessionId),
            method: "PATCH",
            body: body
        )
    }

    /// Soft-delete (archive) a chat session
    func deleteSession(sessionId: Int) async throws {
        try await client.requestVoid(
            APIEndpoints.chatSession(id: sessionId),
            method: "DELETE"
        )
    }

    // MARK: - Messaging

    /// Send a message and start async processing
    /// Returns immediately with the pending message info
    func sendMessageAsync(
        sessionId: Int,
        message: String
    ) async throws -> SendChatMessageResponse {
        let request = SendChatMessageRequest(message: message)
        let encoder = JSONEncoder()
        let body = try encoder.encode(request)

        return try await client.request(
            APIEndpoints.chatMessages(sessionId: sessionId),
            method: "POST",
            body: body
        )
    }

    /// Create or continue a contextual assistant turn.
    func createAssistantTurn(
        message: String,
        sessionId: Int? = nil,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse {
        let request = AssistantTurnRequest(
            message: message,
            sessionId: sessionId,
            screenContext: screenContext
        )
        let body = try JSONEncoder().encode(request)
        return try await client.request(
            APIEndpoints.assistantTurns,
            method: "POST",
            body: body
        )
    }

    /// Poll for message status
    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse {
        return try await client.request(
            APIEndpoints.chatMessageStatus(messageId: messageId)
        )
    }

    /// Poll until a pending assistant message completes.
    func waitForMessageCompletion(messageId: Int) async throws -> ChatMessage {
        var attempts = 0
        while attempts < maxPollingAttempts {
            try Task.checkCancellation()

            let status = try await getMessageStatus(messageId: messageId)
            switch status.status {
            case .completed:
                guard let assistantMessage = status.assistantMessage else {
                    throw ChatServiceError.missingAssistantMessage
                }
                return assistantMessage
            case .failed:
                throw ChatServiceError.processingFailed(status.error ?? "Unknown error")
            case .processing:
                attempts += 1
                try await Task.sleep(nanoseconds: pollingInterval)
            }
        }

        throw ChatServiceError.timeout
    }

    /// Send a message and wait for the assistant response (polls for completion)
    /// This is the main method for sending messages - it handles the async pattern internally
    func sendMessage(
        sessionId: Int,
        message: String,
        onProcessingStarted: ((ChatMessage) -> Void)? = nil
    ) async throws -> ChatMessage {
        // Send the message - returns immediately
        let response = try await sendMessageAsync(sessionId: sessionId, message: message)

        // Notify caller that processing has started (for immediate UI update)
        if let callback = onProcessingStarted {
            callback(response.userMessage)
        }

        // Poll for completion
        return try await waitForMessageCompletion(messageId: response.messageId)
    }

    /// Get initial follow-up question suggestions for an article-based session (non-streaming)
    func getInitialSuggestions(
        sessionId: Int
    ) async throws -> ChatMessage {
        let response: InitialSuggestionsResponse = try await client.request(
            APIEndpoints.chatInitialSuggestions(sessionId: sessionId),
            method: "POST"
        )

        return ChatMessage(
            id: response.id,
            role: response.role,
            timestamp: response.timestamp,
            content: response.content
        )
    }

    // MARK: - Convenience Methods

    /// Start a deep dive chat for an article
    func startArticleChat(
        contentId: Int,
        provider: ChatModelProvider = .anthropic
    ) async throws -> ChatSessionSummary {
        // Check for existing session
        if let existing = try await getSessionForContent(contentId: contentId) {
            return existing
        }

        // Create new session
        return try await createSession(
            contentId: contentId,
            provider: provider
        )
    }

    /// Start a topic-focused chat for an article
    func startTopicChat(
        contentId: Int,
        topic: String,
        provider: ChatModelProvider = .anthropic
    ) async throws -> ChatSessionSummary {
        return try await createSession(
            contentId: contentId,
            topic: topic,
            provider: provider
        )
    }

    /// Start an ad-hoc chat without article context
    func startAdHocChat(
        initialMessage: String? = nil,
        provider: ChatModelProvider = .anthropic
    ) async throws -> ChatSessionSummary {
        return try await createSession(
            provider: provider,
            initialMessage: initialMessage
        )
    }

    /// Start a deep research session for an article
    /// Deep research uses OpenAI's o4-mini-deep-research model for comprehensive research
    func startDeepResearch(
        contentId: Int? = nil,
        topic: String? = nil
    ) async throws -> ChatSessionSummary {
        return try await createSession(
            contentId: contentId,
            topic: topic,
            provider: .deep_research
        )
    }
}
