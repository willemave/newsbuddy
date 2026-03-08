//
//  ChatSessionDetail.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation

/// Full chat session details with message history
struct ChatSessionDetail: Codable {
    let session: ChatSessionSummary
    let messages: [ChatMessage]
}

/// Response from creating a new chat session
struct CreateChatSessionResponse: Codable {
    let session: ChatSessionSummary
}

/// Response after sending a message (async)
/// Returns immediately with user message and message_id to poll for completion
struct SendChatMessageResponse: Codable {
    let sessionId: Int
    let userMessage: ChatMessage
    let messageId: Int
    let status: MessageProcessingStatus

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case userMessage = "user_message"
        case messageId = "message_id"
        case status
    }
}

/// Response returned after starting a daily-digest dig-deeper chat
struct StartDailyDigestChatResponse: Codable {
    let session: ChatSessionSummary
    let userMessage: ChatMessage
    let messageId: Int
    let status: MessageProcessingStatus

    enum CodingKeys: String, CodingKey {
        case session
        case userMessage = "user_message"
        case messageId = "message_id"
        case status
    }
}

/// Response when polling for message completion status
struct MessageStatusResponse: Codable {
    let messageId: Int
    let status: MessageProcessingStatus
    let assistantMessage: ChatMessage?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case messageId = "message_id"
        case status
        case assistantMessage = "assistant_message"
        case error
    }

    var isCompleted: Bool {
        status == .completed
    }

    var isProcessing: Bool {
        status == .processing
    }

    var hasFailed: Bool {
        status == .failed
    }
}

/// Response for initial suggestions (non-streaming)
struct InitialSuggestionsResponse: Codable {
    let id: Int
    let sessionId: Int
    let role: ChatMessageRole
    let content: String
    let timestamp: String

    enum CodingKeys: String, CodingKey {
        case id
        case sessionId = "session_id"
        case role
        case content
        case timestamp
    }
}

/// Request to create a new chat session
struct CreateChatSessionRequest: Codable {
    var contentId: Int?
    var topic: String?
    var llmProvider: String?
    var llmModelHint: String?
    var initialMessage: String?

    enum CodingKeys: String, CodingKey {
        case contentId = "content_id"
        case topic
        case llmProvider = "llm_provider"
        case llmModelHint = "llm_model_hint"
        case initialMessage = "initial_message"
    }
}

/// Request to send a message in a chat session
struct SendChatMessageRequest: Codable {
    let message: String
}
