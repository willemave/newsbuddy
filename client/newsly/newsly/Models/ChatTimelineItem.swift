//
//  ChatTimelineItem.swift
//  newsly
//

import Foundation

enum ChatTimelineID: Hashable, Sendable {
    case server(displayKey: String)
    case local(UUID)

    static func server(for message: ChatMessage) -> ChatTimelineID {
        if let displayKey = message.displayKey, !displayKey.isEmpty {
            return .server(displayKey: displayKey)
        }
        return .server(
            sourceMessageId: message.sourceMessageId ?? message.id,
            role: message.role,
            displayType: message.displayType
        )
    }

    static func server(
        sourceMessageId: Int,
        role: APIChatMessageRole,
        displayType: APIChatMessageDisplayType
    ) -> ChatTimelineID {
        .server(
            displayKey: Self.legacyDisplayKey(
                sourceMessageId: sourceMessageId,
                role: role,
                displayType: displayType
            )
        )
    }

    var sortKey: String {
        switch self {
        case .server(let displayKey):
            return displayKey
        case .local(let uuid):
            return "local|\(uuid.uuidString)"
        }
    }

    private static func legacyDisplayKey(
        sourceMessageId: Int,
        role: APIChatMessageRole,
        displayType: APIChatMessageDisplayType
    ) -> String {
        "server|\(sourceMessageId)|\(role.rawValue)|\(displayType.rawValue)"
    }
}

struct ChatTimelineItem: Identifiable, Equatable {
    let id: ChatTimelineID
    var message: ChatMessage
    var pendingMessageId: Int?
    var retryText: String?
    var isQueued: Bool = false

    func isOrderedBefore(_ other: ChatTimelineItem) -> Bool {
        let lhsKey = (
            message.timestamp,
            message.sourceMessageId ?? message.id,
            message.turnSortOrder,
            id.sortKey
        )
        let rhsKey = (
            other.message.timestamp,
            other.message.sourceMessageId ?? other.message.id,
            other.message.turnSortOrder,
            other.id.sortKey
        )
        return lhsKey < rhsKey
    }
}

private extension ChatMessage {
    var turnSortOrder: Int {
        if isUser {
            return 0
        }
        if isProcessSummary {
            return 1
        }
        if isAssistant {
            return 2
        }

        switch role {
        case .tool:
            return 1
        case .system:
            return 3
        case .user:
            return 0
        case .assistant:
            return 2
        }
    }
}
