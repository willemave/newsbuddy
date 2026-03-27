//
//  ChatSessionRoute.swift
//  newsly
//
//  Created by Assistant on 12/6/25.
//

import Foundation

struct ChatSessionRoute: Hashable {
    let sessionId: Int
    let contentId: Int?
    let initialUserMessageText: String?
    let initialUserMessageTimestamp: String?
    let pendingMessageId: Int?

    init(
        sessionId: Int,
        contentId: Int? = nil,
        initialUserMessageText: String? = nil,
        initialUserMessageTimestamp: String? = nil,
        pendingMessageId: Int? = nil
    ) {
        self.sessionId = sessionId
        self.contentId = contentId
        self.initialUserMessageText = initialUserMessageText
        self.initialUserMessageTimestamp = initialUserMessageTimestamp
        self.pendingMessageId = pendingMessageId
    }
}
