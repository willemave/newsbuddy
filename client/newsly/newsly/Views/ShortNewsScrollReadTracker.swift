//
//  ShortNewsScrollReadTracker.swift
//  newsly
//

@MainActor
final class ShortNewsScrollReadTracker {
    private var topVisibleItemId: Int?
    private var markedAsReadIds: Set<Int> = []

    func updateTopVisibleItemId(_ itemId: Int?) {
        topVisibleItemId = itemId
    }

    func idsToMarkAboveTop(in items: [ContentSummary]) -> [Int] {
        guard let topVisibleItemId,
              let topIndex = items.firstIndex(where: { $0.id == topVisibleItemId })
        else {
            return []
        }

        let idsToMark = items.prefix(topIndex).compactMap { item -> Int? in
            guard !item.isRead, !markedAsReadIds.contains(item.id) else { return nil }
            return item.id
        }

        markedAsReadIds.formUnion(idsToMark)
        return idsToMark
    }
}
