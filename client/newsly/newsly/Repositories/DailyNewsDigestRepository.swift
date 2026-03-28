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
    func startDigDeeperChat(id: Int) async throws -> StartDailyDigestChatResponse
    func startBulletDigDeeperChat(
        digestId: Int,
        bulletIndex: Int
    ) async throws -> StartDailyDigestChatResponse
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

    func startDigDeeperChat(id: Int) async throws -> StartDailyDigestChatResponse {
        try await client.request(
            APIEndpoints.dailyDigestDigDeeper(id: id),
            method: "POST"
        )
    }

    func startBulletDigDeeperChat(
        digestId: Int,
        bulletIndex: Int
    ) async throws -> StartDailyDigestChatResponse {
        try await client.request(
            APIEndpoints.dailyDigestBulletDigDeeper(id: digestId, bulletIndex: bulletIndex),
            method: "POST"
        )
    }
}
