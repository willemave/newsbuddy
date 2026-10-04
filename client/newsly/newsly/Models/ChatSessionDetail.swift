//
//  ChatSessionDetail.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation

/// Full chat session details with message history
struct ChatSessionDetail {
    let session: ChatSessionSummary
    let messages: [ChatMessage]

    init(session: ChatSessionSummary, messages: [ChatMessage]) {
        self.session = session
        self.messages = messages
    }

    init(api response: APIChatSessionDetail) {
        self.init(
            session: ChatSessionSummary(api: response.session),
            messages: response.messages.map(ChatMessage.init(api:))
        )
    }
}

/// Response after sending a message (async)
/// Returns immediately with user message and message_id to poll for completion
struct SendChatMessageResponse {
    let sessionId: Int
    let userMessage: ChatMessage
    let messageId: Int
    let status: APIMessageProcessingStatus

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

    init(api response: APISendMessageResponse) {
        self.init(
            sessionId: response.sessionId,
            userMessage: ChatMessage(api: response.userMessage),
            messageId: response.messageId,
            status: response.status
        )
    }

}

/// Response when polling for message completion status
struct MessageStatusResponse {
    let messageId: Int
    let status: APIMessageProcessingStatus
    let assistantMessage: ChatMessage?
    let partialAssistantMessage: ChatMessage?
    let streamGeneration: Int?
    let streamRevision: Int?
    let error: String?

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

    init(api response: APIMessageStatusResponse) {
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

    var api: APIAssistantScreenContext {
        APIAssistantScreenContext(
            screenType: screenType,
            screenTitle: screenTitle,
            contentId: contentId,
            newsItemId: newsItemId,
            visibleContentIds: visibleContentIds,
            visibleNewsItemIds: visibleNewsItemIds,
            selectedTopic: selectedTopic,
            query: query,
            note: note,
            assistantAction: assistantAction
        )
    }
}

struct AssistantTurnResponse {
    let session: ChatSessionSummary
    let userMessage: ChatMessage
    let messageId: Int
    let status: APIMessageProcessingStatus

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

    init(api response: APIAssistantTurnResponse) {
        self.init(
            session: ChatSessionSummary(api: response.session),
            userMessage: ChatMessage(api: response.userMessage),
            messageId: response.messageId,
            status: response.status
        )
    }
}
