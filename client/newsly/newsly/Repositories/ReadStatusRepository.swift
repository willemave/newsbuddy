//
//  ReadStatusRepository.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ReadStatus")

enum ReadStatusEndpoint {
    case content
    case newsItems

    var path: String {
        switch self {
        case .content:
            return APIEndpoints.bulkMarkRead
        case .newsItems:
            return APIEndpoints.newsItemsMarkRead
        }
    }
}

protocol ReadStatusRepositoryType {
    func markRead(ids: [Int]) async throws
}

final class ReadStatusRepository: ReadStatusRepositoryType {
    private let client: APIClient
    private let encoder = JSONEncoder()
    private let endpoint: ReadStatusEndpoint

    init(client: APIClient = .shared, endpoint: ReadStatusEndpoint = .content) {
        self.client = client
        self.endpoint = endpoint
    }

    func markRead(ids: [Int]) async throws {
        guard !ids.isEmpty else {
            logger.debug("[ReadStatus] markRead called with empty ids, skipping")
            return
        }

        logger.info("[ReadStatus] markRead called | ids=\(ids, privacy: .public) count=\(ids.count)")

        let payload = APIBulkMarkReadRequest(contentIds: ids)
        let body = try encoder.encode(payload)

        do {
            let _: BulkMarkReadResponse = try await client.request(
                endpoint.path,
                method: .post,
                body: body
            )
            logger.info("[ReadStatus] markRead success | ids=\(ids, privacy: .public)")
        } catch {
            logger.error("[ReadStatus] markRead failed | ids=\(ids, privacy: .public) error=\(error.localizedDescription)")
            throw error
        }
    }
}
