//
//  SubmissionStatusViewModel.swift
//  newsly
//
//  Created by Assistant on 1/14/26.
//

import Foundation
import Observation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "SubmissionStatusViewModel")

@MainActor
@Observable
final class SubmissionStatusViewModel {
    private enum StorageKey {
        static let lastViewedSubmissionCreatedAt = "lastViewedSubmissionCreatedAt"
    }

    var submissions: [SubmissionStatusItem] {
        get { feed.items }
        set { feed.replaceItems(newValue) }
    }

    var isLoading: Bool {
        feed.phase == .initialLoading
    }

    var isLoadingMore: Bool {
        feed.phase == .loadingMore
    }

    var errorMessage: String? {
        guard case .error(let message) = feed.phase else { return nil }
        return message
    }

    var nextCursor: String? {
        feed.nextCursor
    }

    var hasMore: Bool {
        feed.hasMore
    }

    private var lastViewedSubmissionCreatedAt: Date? {
        didSet {
            if let lastViewedSubmissionCreatedAt {
                defaults.set(lastViewedSubmissionCreatedAt.timeIntervalSince1970, forKey: StorageKey.lastViewedSubmissionCreatedAt)
            } else {
                defaults.removeObject(forKey: StorageKey.lastViewedSubmissionCreatedAt)
            }
        }
    }

    private let feed: PaginatedFeed<SubmissionStatusItem>

    @ObservationIgnored
    private let defaults: UserDefaults

    init(
        defaults: UserDefaults = SharedContainer.userDefaults,
        loadPage: @escaping (_ cursor: String?) async throws -> SubmissionStatusFeed
    ) {
        self.defaults = defaults
        let timestamp = defaults.double(forKey: StorageKey.lastViewedSubmissionCreatedAt)
        self.lastViewedSubmissionCreatedAt = timestamp > 0 ? Date(timeIntervalSince1970: timestamp) : nil
        self.feed = PaginatedFeed { cursor in
            let response = try await loadPage(cursor)
            return Page(
                items: response.submissions,
                nextCursor: response.nextCursor,
                hasMore: response.hasMore
            )
        }
    }

    var unseenCount: Int {
        guard let lastViewedAt = lastViewedSubmissionCreatedAt else {
            return submissions.count
        }

        return submissions.reduce(into: 0) { count, submission in
            if let createdDate = submission.createdDate, createdDate > lastViewedAt {
                count += 1
            }
        }
    }

    func load() async {
        guard !isLoading else { return }
        await feed.loadInitial()
        logErrorIfNeeded(operation: "load")
    }

    func loadMore() async {
        guard !isLoadingMore, hasMore, nextCursor != nil else { return }
        await feed.loadNextPage()
        logErrorIfNeeded(operation: "loadMore")
    }

    func markCurrentSubmissionsViewed() {
        let latestVisibleDate = submissions.compactMap(\.createdDate).max()
        let viewedAt = latestVisibleDate ?? Date()

        if let lastViewedSubmissionCreatedAt, lastViewedSubmissionCreatedAt >= viewedAt {
            return
        }

        lastViewedSubmissionCreatedAt = viewedAt
    }

    private func logErrorIfNeeded(operation: String) {
        guard let errorMessage else { return }
        logger.error("[SubmissionStatusViewModel] \(operation) failed | error=\(errorMessage)")
    }
}
