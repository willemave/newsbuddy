//
//  ReadStateCache.swift
//  newsly
//

import Foundation
import Observation
import os.log

private let readStateCacheLogger = Logger(subsystem: "com.newsly", category: "ReadStateCache")

struct ReadStateKey: Hashable, Sendable {
    let id: Int
    let contentType: APIContentType

    init(id: Int, contentType: APIContentType) {
        self.id = id
        self.contentType = contentType
    }

    init(_ item: ContentSummary) {
        self.id = item.id
        self.contentType = item.contentType
    }

    var usesNewsEndpoint: Bool {
        contentType == .news
    }
}

@MainActor
@Observable
final class ReadStateCache {
    private(set) var readKeys: Set<ReadStateKey> = []

    @ObservationIgnored
    private let contentReadRepository: ReadStatusRepositoryType

    @ObservationIgnored
    private let newsReadRepository: ReadStatusRepositoryType

    @ObservationIgnored
    private let badgeStatsStore: BadgeStatsStore

    init(
        contentReadRepository: ReadStatusRepositoryType = ReadStatusRepository(),
        newsReadRepository: ReadStatusRepositoryType = ReadStatusRepository(endpoint: .newsItems),
        badgeStatsStore: BadgeStatsStore? = nil
    ) {
        self.contentReadRepository = contentReadRepository
        self.newsReadRepository = newsReadRepository
        self.badgeStatsStore = badgeStatsStore ?? .shared
    }

    func isRead(id: Int, contentType: APIContentType) -> Bool {
        readKeys.contains(ReadStateKey(id: id, contentType: contentType))
    }

    func applying(to item: ContentSummary) -> ContentSummary {
        isRead(id: item.id, contentType: item.contentType)
            ? item.updating(isRead: true)
            : item
    }

    func applying(
        to items: [ContentSummary],
        removeReadItems: Bool
    ) -> [ContentSummary] {
        var projectedItems = items.map(applying(to:))
        if removeReadItems {
            projectedItems.removeAll { $0.isRead }
        }
        return projectedItems
    }

    @discardableResult
    func markReadLocally(
        _ keys: Set<ReadStateKey>,
        adjustUnreadCounts: Bool = true
    ) -> Set<ReadStateKey> {
        let newlyReadKeys = keys.subtracting(readKeys)
        guard !newlyReadKeys.isEmpty else { return [] }

        readKeys.formUnion(newlyReadKeys)
        if adjustUnreadCounts {
            decrementUnreadCounts(for: newlyReadKeys)
        }
        return newlyReadKeys
    }

    func rollbackRead(
        _ keys: Set<ReadStateKey>,
        adjustUnreadCounts: Bool = true
    ) {
        let rollbackKeys = keys.intersection(readKeys)
        guard !rollbackKeys.isEmpty else { return }

        readKeys.subtract(rollbackKeys)
        if adjustUnreadCounts {
            incrementUnreadCounts(for: rollbackKeys)
        }
    }

    @discardableResult
    func markReadAndSync(
        _ keys: Set<ReadStateKey>,
        adjustUnreadCounts: Bool = true
    ) async throws -> Set<ReadStateKey> {
        let newlyReadKeys = markReadLocally(keys, adjustUnreadCounts: adjustUnreadCounts)
        guard !newlyReadKeys.isEmpty else { return [] }

        do {
            try await syncRead(newlyReadKeys)
            return newlyReadKeys
        } catch {
            rollbackRead(newlyReadKeys, adjustUnreadCounts: adjustUnreadCounts)
            throw error
        }
    }

    private func syncRead(_ keys: Set<ReadStateKey>) async throws {
        let newsIds = keys.filter(\.usesNewsEndpoint).map(\.id).sorted()
        let contentIds = keys.filter { !$0.usesNewsEndpoint }.map(\.id).sorted()

        if !contentIds.isEmpty {
            readStateCacheLogger.info("[ReadStateCache] Syncing content read state | ids=\(contentIds, privacy: .public)")
            try await contentReadRepository.markRead(ids: contentIds)
        }

        if !newsIds.isEmpty {
            readStateCacheLogger.info("[ReadStateCache] Syncing news read state | ids=\(newsIds, privacy: .public)")
            try await newsReadRepository.markRead(ids: newsIds)
        }
    }

    private func decrementUnreadCounts(for keys: Set<ReadStateKey>) {
        let counts = countsByType(for: keys)
        badgeStatsStore.decrementArticleCount(by: counts.articles)
        badgeStatsStore.decrementPodcastCount(by: counts.podcasts)
    }

    private func incrementUnreadCounts(for keys: Set<ReadStateKey>) {
        let counts = countsByType(for: keys)
        badgeStatsStore.incrementArticleCount(by: counts.articles)
        badgeStatsStore.incrementPodcastCount(by: counts.podcasts)
    }

    private func countsByType(for keys: Set<ReadStateKey>) -> (articles: Int, podcasts: Int) {
        keys.reduce(into: (articles: 0, podcasts: 0)) { partial, key in
            switch key.contentType {
            case .article:
                partial.articles += 1
            case .podcast:
                partial.podcasts += 1
            default:
                break
            }
        }
    }
}
