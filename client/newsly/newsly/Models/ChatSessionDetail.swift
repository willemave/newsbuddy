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

    init(session: ChatSessionSummary, messages: [ChatMessage]) {
        self.session = session
        self.messages = messages
    }

    init(from decoder: Decoder) throws {
        let response = try APIChatSessionDetail(from: decoder)
        self.init(
            session: ChatSessionSummary(api: response.session),
            messages: response.messages.map(ChatMessage.init(api:))
        )
    }
}

/// Response from creating a new chat session
struct CreateChatSessionResponse: Codable {
    let session: ChatSessionSummary

    init(session: ChatSessionSummary) {
        self.session = session
    }

    init(from decoder: Decoder) throws {
        let response = try APICreateChatSessionResponse(from: decoder)
        self.init(session: ChatSessionSummary(api: response.session))
    }
}

/// Response after sending a message (async)
/// Returns immediately with user message and message_id to poll for completion
struct SendChatMessageResponse: Codable {
    let sessionId: Int
    let userMessage: ChatMessage
    let messageId: Int
    let status: APIMessageProcessingStatus

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case userMessage = "user_message"
        case messageId = "message_id"
        case status
    }

    init(
        sessionId: Int,
        userMessage: ChatMessage,
        messageId: Int,
        status: APIMessageProcessingStatus
    ) {
        self.sessionId = sessionId
        self.userMessage = userMessage
        self.messageId = messageId
        self.status = status
    }

    init(from decoder: Decoder) throws {
        let response = try APISendMessageResponse(from: decoder)
        self.init(
            sessionId: response.sessionId,
            userMessage: ChatMessage(api: response.userMessage),
            messageId: response.messageId,
            status: response.status
        )
    }
}

/// Response when polling for message completion status
struct MessageStatusResponse: Codable {
    let messageId: Int
    let status: APIMessageProcessingStatus
    let assistantMessage: ChatMessage?
    let partialAssistantMessage: ChatMessage?
    let streamGeneration: Int?
    let streamRevision: Int?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case messageId = "message_id"
        case status
        case assistantMessage = "assistant_message"
        case partialAssistantMessage = "partial_assistant_message"
        case streamGeneration = "stream_generation"
        case streamRevision = "stream_revision"
        case error
    }

    init(
        messageId: Int,
        status: APIMessageProcessingStatus,
        assistantMessage: ChatMessage? = nil,
        partialAssistantMessage: ChatMessage? = nil,
        streamGeneration: Int? = nil,
        streamRevision: Int? = nil,
        error: String? = nil
    ) {
        self.messageId = messageId
        self.status = status
        self.assistantMessage = assistantMessage
        self.partialAssistantMessage = partialAssistantMessage
        self.streamGeneration = streamGeneration
        self.streamRevision = streamRevision
        self.error = error
    }

    init(from decoder: Decoder) throws {
        let response = try APIMessageStatusResponse(from: decoder)
        self.init(
            messageId: response.messageId,
            status: response.status,
            assistantMessage: response.assistantMessage.map(ChatMessage.init(api:)),
            partialAssistantMessage: response.partialAssistantMessage.map(ChatMessage.init(api:)),
            streamGeneration: response.streamGeneration,
            streamRevision: response.streamRevision,
            error: response.error
        )
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
    let role: APIChatMessageRole
    let content: String
    let timestamp: Date

    enum CodingKeys: String, CodingKey {
        case id
        case sessionId = "session_id"
        case role
        case content
        case timestamp
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        sessionId = try container.decode(Int.self, forKey: .sessionId)
        role = try container.decode(APIChatMessageRole.self, forKey: .role)
        content = try container.decode(String.self, forKey: .content)
        let timestampRaw = try container.decode(String.self, forKey: .timestamp)
        guard let timestampParsed = ServerDate.parse(timestampRaw) else {
            throw DecodingError.dataCorruptedError(
                forKey: .timestamp,
                in: container,
                debugDescription: "Unparseable date for timestamp"
            )
        }
        timestamp = timestampParsed
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(sessionId, forKey: .sessionId)
        try container.encode(role, forKey: .role)
        try container.encode(content, forKey: .content)
        try container.encode(ServerDate.format(timestamp), forKey: .timestamp)
    }
}

/// Request to create a new chat session
struct CreateChatSessionRequest: Codable {
    var contentId: Int?
    var newsItemId: Int?
    var topic: String?
    var llmProvider: String?
    var llmModelHint: String?
    var initialMessage: String?

    enum CodingKeys: String, CodingKey {
        case contentId = "content_id"
        case newsItemId = "news_item_id"
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

struct StartCouncilChatRequest: Codable {
    let message: String
}

struct SelectCouncilBranchRequest: Codable {
    let childSessionId: Int

    enum CodingKeys: String, CodingKey {
        case childSessionId = "child_session_id"
    }
}

struct RetryCouncilBranchRequest: Codable {
    let childSessionId: Int

    enum CodingKeys: String, CodingKey {
        case childSessionId = "child_session_id"
    }
}

struct AssistantScreenContext: Codable, Equatable {
    private static let maxVisibleContentIds = 15
    private static let maxNoteLength = 1_500

    let screenType: String
    let screenTitle: String?
    let contentId: Int?
    let newsItemId: Int?
    let visibleContentIds: [Int]
    let visibleNewsItemIds: [Int]
    let selectedTopic: String?
    let query: String?
    let note: String?
    let assistantAction: String?

    init(
        screenType: String,
        screenTitle: String? = nil,
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        visibleContentIds: [Int] = [],
        visibleNewsItemIds: [Int] = [],
        selectedTopic: String? = nil,
        query: String? = nil,
        note: String? = nil,
        assistantAction: String? = nil
    ) {
        self.screenType = screenType
        self.screenTitle = screenTitle
        self.contentId = contentId
        self.newsItemId = newsItemId
        self.visibleContentIds = Array(visibleContentIds.prefix(Self.maxVisibleContentIds))
        self.visibleNewsItemIds = Array(visibleNewsItemIds.prefix(Self.maxVisibleContentIds))
        self.selectedTopic = selectedTopic
        self.query = query
        self.note = note.map { String($0.prefix(Self.maxNoteLength)) }
        self.assistantAction = assistantAction
    }

    enum CodingKeys: String, CodingKey {
        case screenType = "screen_type"
        case screenTitle = "screen_title"
        case contentId = "content_id"
        case newsItemId = "news_item_id"
        case visibleContentIds = "visible_content_ids"
        case visibleNewsItemIds = "visible_news_item_ids"
        case selectedTopic = "selected_topic"
        case query
        case note
        case assistantAction = "assistant_action"
    }
}

struct AssistantTurnRequest: Codable {
    let message: String
    let sessionId: Int?
    let screenContext: AssistantScreenContext

    enum CodingKeys: String, CodingKey {
        case message
        case sessionId = "session_id"
        case screenContext = "screen_context"
    }
}

struct AssistantTurnResponse: Codable {
    let session: ChatSessionSummary
    let userMessage: ChatMessage
    let messageId: Int
    let status: APIMessageProcessingStatus

    enum CodingKeys: String, CodingKey {
        case session
        case userMessage = "user_message"
        case messageId = "message_id"
        case status
    }

    init(
        session: ChatSessionSummary,
        userMessage: ChatMessage,
        messageId: Int,
        status: APIMessageProcessingStatus
    ) {
        self.session = session
        self.userMessage = userMessage
        self.messageId = messageId
        self.status = status
    }

    init(from decoder: Decoder) throws {
        let response = try APIAssistantTurnResponse(from: decoder)
        self.init(
            session: ChatSessionSummary(api: response.session),
            userMessage: ChatMessage(api: response.userMessage),
            messageId: response.messageId,
            status: response.status
        )
    }
}
