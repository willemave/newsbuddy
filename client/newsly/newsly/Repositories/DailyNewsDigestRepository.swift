//
//  DailyNewsDigestRepository.swift
//  newsly
//

import Combine
import Foundation

protocol DailyNewsDigestRepositoryType {
    func loadPage(
        readFilter: ReadFilter,
        cursor: String?,
        limit: Int?
    ) -> AnyPublisher<DailyNewsDigestListResponse, Error>

    func markRead(id: Int) -> AnyPublisher<Void, Error>
    func markUnread(id: Int) -> AnyPublisher<Void, Error>
    func fetchVoiceSummary(id: Int) async throws -> DailyNewsDigestVoiceSummaryResponse
}

final class DailyNewsDigestRepository: DailyNewsDigestRepositoryType {
    private let client: APIClient
    private let defaultPageSize: Int

    init(client: APIClient = .shared, defaultPageSize: Int = 25) {
        self.client = client
        self.defaultPageSize = defaultPageSize
    }

    func loadPage(
        readFilter: ReadFilter,
        cursor: String?,
        limit: Int? = nil
    ) -> AnyPublisher<DailyNewsDigestListResponse, Error> {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "read_filter", value: readFilter.rawValue),
            URLQueryItem(name: "limit", value: String(limit ?? defaultPageSize))
        ]

        if let cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }

        return client.publisher(
            APIEndpoints.dailyNewsDigests,
            queryItems: queryItems
        )
    }

    func markRead(id: Int) -> AnyPublisher<Void, Error> {
        client.publisherVoid(APIEndpoints.markDailyDigestRead(id: id), method: "POST")
    }

    func markUnread(id: Int) -> AnyPublisher<Void, Error> {
        client.publisherVoid(APIEndpoints.markDailyDigestUnread(id: id), method: "DELETE")
    }

    func fetchVoiceSummary(id: Int) async throws -> DailyNewsDigestVoiceSummaryResponse {
        try await client.request(APIEndpoints.dailyDigestVoiceSummary(id: id))
    }
}
