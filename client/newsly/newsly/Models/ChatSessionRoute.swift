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
    let newsItemId: Int?
    let initialUserMessageText: String?
    let initialUserMessageTimestamp: Date?
    let pendingMessageId: Int?
    let pendingCouncilPrompt: String?
    let focusComposerOnAppear: Bool
    var stableKey: String {
        [
            String(sessionId),
            String(contentId ?? -1),
            String(newsItemId ?? -1),
            initialUserMessageText ?? "",
            initialUserMessageTimestamp.map(ServerDate.format) ?? "",
            pendingMessageId.map(String.init) ?? "",
            pendingCouncilPrompt ?? "",
            String(focusComposerOnAppear)
        ].joined(separator: "|")
    }

    init(
        sessionId: Int,
        session: ChatSessionSummary? = nil,
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        initialUserMessageText: String? = nil,
        initialUserMessageTimestamp: Date? = nil,
        pendingMessageId: Int? = nil,
        pendingCouncilPrompt: String? = nil,
        focusComposerOnAppear: Bool = false
    ) {
        self.sessionId = sessionId
        self.session = session
        self.contentId = contentId
        self.newsItemId = newsItemId
        self.initialUserMessageText = initialUserMessageText
        self.initialUserMessageTimestamp = initialUserMessageTimestamp
        self.pendingMessageId = pendingMessageId
        self.pendingCouncilPrompt = pendingCouncilPrompt
        self.focusComposerOnAppear = focusComposerOnAppear
    }

    init(
        session: ChatSessionSummary,
        initialUserMessageText: String? = nil,
        initialUserMessageTimestamp: Date? = nil,
        pendingMessageId: Int? = nil,
        pendingCouncilPrompt: String? = nil,
        focusComposerOnAppear: Bool = false
    ) {
        self.init(
            sessionId: session.id,
            session: session,
            contentId: session.contentId,
            newsItemId: session.newsItemId,
            initialUserMessageText: initialUserMessageText,
            initialUserMessageTimestamp: initialUserMessageTimestamp,
            pendingMessageId: pendingMessageId,
            pendingCouncilPrompt: pendingCouncilPrompt,
            focusComposerOnAppear: focusComposerOnAppear
        )
    }
}
