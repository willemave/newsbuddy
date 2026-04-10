//
//  SubmissionStatusViewModel.swift
//  newsly
//
//  Created by Assistant on 1/14/26.
//

import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "SubmissionStatusViewModel")

@MainActor
final class SubmissionStatusViewModel: CursorPaginatedViewModel {
    private enum StorageKey {
        static let lastViewedSubmissionCreatedAt = "lastViewedSubmissionCreatedAt"
    }

    @Published var submissions: [SubmissionStatusItem] = []
    @Published var isLoading = false
    @Published var isLoadingMore = false
    @Published var errorMessage: String?
    private let defaults: UserDefaults

    init(defaults: UserDefaults = SharedContainer.userDefaults) {
        self.defaults = defaults
        super.init()
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
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let response = try await ContentService.shared.fetchSubmissionStatusList()
            submissions = response.submissions
            applyPagination(nextCursor: response.nextCursor, hasMore: response.hasMore)
        } catch {
            logger.error("[SubmissionStatusViewModel] load failed | error=\(error.localizedDescription)")
            errorMessage = error.localizedDescription
        }
    }

    func loadMore() async {
        guard !isLoadingMore, hasMore, let cursor = nextCursor else { return }
        isLoadingMore = true
        defer { isLoadingMore = false }

        do {
            let response = try await ContentService.shared.fetchSubmissionStatusList(cursor: cursor)
            submissions.append(contentsOf: response.submissions)
            applyPagination(nextCursor: response.nextCursor, hasMore: response.hasMore)
        } catch {
            logger.error("[SubmissionStatusViewModel] loadMore failed | error=\(error.localizedDescription)")
        }
    }

    func markCurrentSubmissionsViewed() {
        let latestVisibleDate = submissions.compactMap(\.createdDate).max()
        let viewedAt = latestVisibleDate ?? Date()

        if let lastViewedSubmissionCreatedAt, lastViewedSubmissionCreatedAt >= viewedAt {
            return
        }

        defaults.set(viewedAt.timeIntervalSince1970, forKey: StorageKey.lastViewedSubmissionCreatedAt)
        objectWillChange.send()
    }

    private var lastViewedSubmissionCreatedAt: Date? {
        let timestamp = defaults.double(forKey: StorageKey.lastViewedSubmissionCreatedAt)
        guard timestamp > 0 else { return nil }
        return Date(timeIntervalSince1970: timestamp)
    }
}
