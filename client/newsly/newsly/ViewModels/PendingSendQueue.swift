//
//  PendingSendQueue.swift
//  newsly
//

import Foundation

struct PendingSend: Equatable {
    let localId: UUID
    let text: String
    var messageId: Int?
    let createdAt: Date

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

struct PendingSendQueue {
    private var sends: [PendingSend] = []

    var isEmpty: Bool { sends.isEmpty }
    var count: Int { sends.count }

    mutating func enqueue(_ pending: PendingSend) {
        sends.append(pending)
    }

    mutating func dequeue() -> PendingSend? {
        guard !sends.isEmpty else { return nil }
        return sends.removeFirst()
    }

    func contains(localId: UUID) -> Bool {
        sends.contains { $0.localId == localId }
    }
}
