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
        role: ChatMessageRole,
        displayType: ChatMessageDisplayType
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
        role: ChatMessageRole,
        displayType: ChatMessageDisplayType
    ) -> String {
        "server|\(sourceMessageId)|\(role.rawValue)|\(displayType.rawValue)"
    }
}

struct ChatTimelineItem: Identifiable, Equatable {
    let id: ChatTimelineID
    var message: ChatMessage
    var pendingMessageId: Int?
    var retryText: String?
}
