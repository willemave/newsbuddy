//
//  ChatTimelineReconciler.swift
//  newsly
//

import Foundation

struct PendingSend: Equatable {
    let localId: UUID
    let text: String
    var messageId: Int?
    let createdAt: String

    var placeholderMessage: ChatMessage {
        ChatMessage(
            id: placeholderMessageId,
            sourceMessageId: nil,
            role: .user,
            timestamp: createdAt,
            content: text,
            status: .processing
        )
    }

    private var placeholderMessageId: Int {
        let prefix = localId.uuidString.prefix(8)
        return Int(prefix, radix: 16) ?? 0
    }
}

struct ChatTimelineReconciler {
    func reconcile(
        current: [ChatTimelineItem],
        detail: ChatSessionDetail,
        pendingSends: [UUID: PendingSend],
        localIdentityAliases: inout [ChatTimelineID: UUID]
    ) -> [ChatTimelineItem] {
        var byId = Dictionary(uniqueKeysWithValues: current.map { ($0.id, $0) })
        let incoming = detail.messages.filter { !$0.content.isEmpty || $0.hasCouncilCandidates }

        for message in incoming {
            let serverId = ChatTimelineID.server(for: message)
            var aliasedLocalId = localIdentityAliases[serverId]

            if message.isUser, let pending = pendingSends.values.first(where: { pending in
                pending.messageId == message.sourceMessageId || pending.messageId == message.id
            }) {
                localIdentityAliases[serverId] = pending.localId
                aliasedLocalId = pending.localId
            }

            let rowId = aliasedLocalId.map(ChatTimelineID.local) ?? serverId
            let matchingPending = aliasedLocalId.flatMap { localId in
                pendingSends.values.first { $0.localId == localId }
            }
            byId[rowId] = ChatTimelineItem(
                id: rowId,
                message: message,
                pendingMessageId: matchingPending?.messageId,
                retryText: nil
            )
        }

        for pending in pendingSends.values where pending.messageId == nil {
            let localId = ChatTimelineID.local(pending.localId)
            if byId[localId] == nil {
                byId[localId] = ChatTimelineItem(
                    id: localId,
                    message: pending.placeholderMessage,
                    pendingMessageId: nil,
                    retryText: pending.text
                )
            }
        }

        return byId.values
            .filter { item in
                guard case .local(let uuid) = item.id else { return true }
                return pendingSends[uuid] != nil
                    || localIdentityAliases.values.contains(uuid)
                    || item.retryText != nil
            }
            .sorted { lhs, rhs in
                (lhs.message.timestamp, lhs.id.sortKey) < (rhs.message.timestamp, rhs.id.sortKey)
            }
    }
}
