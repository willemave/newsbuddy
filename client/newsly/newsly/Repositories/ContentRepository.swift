//
//  ContentRepository.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Foundation

protocol ContentRepositoryType {
    func loadPage(
        contentTypes: [APIContentType],
        readFilter: ReadFilter,
        cursor: String?,
        limit: Int?
    ) async throws -> ContentListResponse

    func loadDetail(id: Int) async throws -> ContentDetail
}

final class ContentRepository: ContentRepositoryType {
    private let client: APIClient
    private let defaultPageSize: Int
    private let includeAvailableDates: Bool

    init(
        client: APIClient = .shared,
        defaultPageSize: Int = 25,
        includeAvailableDates: Bool = true
    ) {
        self.client = client
        self.defaultPageSize = defaultPageSize
        self.includeAvailableDates = includeAvailableDates
    }

    func loadPage(
        contentTypes: [APIContentType],
        readFilter: ReadFilter,
        cursor: String?,
        limit: Int? = nil
    ) async throws -> ContentListResponse {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "read_filter", value: readFilter.rawValue),
            URLQueryItem(name: "limit", value: String(limit ?? defaultPageSize))
        ]
        if !includeAvailableDates {
            queryItems.append(URLQueryItem(name: "include_available_dates", value: "false"))
        }
        let isNewsOnly = contentTypes == [.news]

        if !isNewsOnly {
            contentTypes.forEach { type in
                queryItems.append(URLQueryItem(name: "content_type", value: type.rawValue))
            }
        }

        if let cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }

        return try await client.request(
            isNewsOnly ? APIEndpoints.newsItems : APIEndpoints.contentList,
            queryItems: queryItems
        )
    }

    func loadDetail(id: Int) async throws -> ContentDetail {
        try await client.request(APIEndpoints.contentDetail(id: id))
    }
}
