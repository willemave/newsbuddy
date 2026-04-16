//
//  ChatSessionRoute.swift
//  newsly
//
//  Created by Assistant on 12/6/25.
//

import Foundation

struct ChatSessionRoute: Hashable {
    let sessionId: Int
    let session: ChatSessionSummary?
    let contentId: Int?
    let initialUserMessageText: String?
    let initialUserMessageTimestamp: String?
    let pendingMessageId: Int?
    let pendingCouncilPrompt: String?
    var stableKey: String {
        [
            String(sessionId),
            String(contentId ?? -1),
            initialUserMessageText ?? "",
            initialUserMessageTimestamp ?? "",
            pendingMessageId.map(String.init) ?? "",
            pendingCouncilPrompt ?? ""
        ].joined(separator: "|")
    }

    init(
        sessionId: Int,
        session: ChatSessionSummary? = nil,
        contentId: Int? = nil,
        initialUserMessageText: String? = nil,
        initialUserMessageTimestamp: String? = nil,
        pendingMessageId: Int? = nil,
        pendingCouncilPrompt: String? = nil
    ) {
        self.sessionId = sessionId
        self.session = session
        self.contentId = contentId
        self.initialUserMessageText = initialUserMessageText
        self.initialUserMessageTimestamp = initialUserMessageTimestamp
        self.pendingMessageId = pendingMessageId
        self.pendingCouncilPrompt = pendingCouncilPrompt
    }

    init(
        session: ChatSessionSummary,
        initialUserMessageText: String? = nil,
        initialUserMessageTimestamp: String? = nil,
        pendingMessageId: Int? = nil,
        pendingCouncilPrompt: String? = nil
    ) {
        self.init(
            sessionId: session.id,
            session: session,
            contentId: session.contentId,
            initialUserMessageText: initialUserMessageText,
            initialUserMessageTimestamp: initialUserMessageTimestamp,
            pendingMessageId: pendingMessageId,
            pendingCouncilPrompt: pendingCouncilPrompt
        )
    }
}
