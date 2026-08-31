//
//  ScraperConfigService.swift
//  newsly
//

import Foundation

enum ScraperConfigServiceError: LocalizedError {
    case unsupportedScraperType(String)

    var errorDescription: String? {
        switch self {
        case .unsupportedScraperType(let value):
            "Unsupported scraper type: \(value)"
        }
    }
}

class ScraperConfigService {
    static let shared = ScraperConfigService()
    private let client = APIClient.shared

    private init() {}

    func listConfigs(types: [String]? = nil, includeStats: Bool = true) async throws -> [ScraperConfig] {
        var queryItems: [URLQueryItem] = []
        if let types, !types.isEmpty {
            let typeParam = types.joined(separator: ",")
            queryItems.append(URLQueryItem(name: "types", value: typeParam))
        }
        if !includeStats {
            queryItems.append(URLQueryItem(name: "include_stats", value: "false"))
        }
        let configs: [ScraperConfig] = try await client.request(
            APIEndpoints.scraperConfigs,
            queryItems: queryItems.isEmpty ? nil : queryItems
        )
        return configs
    }

    func createConfig(
        scraperType: String,
        displayName: String?,
        feedURL: String,
        limit: Int?,
        isActive: Bool
    ) async throws -> ScraperConfig {
        guard let typedScraperType = APIScraperType(rawValue: scraperType) else {
            throw ScraperConfigServiceError.unsupportedScraperType(scraperType)
        }
        let payload = APICreateUserScraperConfig(
            scraperType: typedScraperType,
            displayName: displayName,
            config: scraperConfig(feedURL: feedURL, limit: limit),
            isActive: isActive
        )
        let body = try JSONEncoder().encode(payload)
        let response: APIScraperConfigResponse = try await client.request(
            APIEndpoints.scraperConfigs,
            method: .post,
            body: body
        )
        return response
    }

    func updateConfig(
        configId: Int,
        displayName: String?,
        feedURL: String?,
        limit: Int?,
        isActive: Bool?
    ) async throws -> ScraperConfig {
        let config = (feedURL != nil || limit != nil)
            ? scraperConfig(feedURL: feedURL, limit: limit)
            : nil
        let payload = APIUpdateUserScraperConfig(
            displayName: displayName,
            config: config,
            isActive: isActive
        )
        let body = try JSONEncoder().encode(payload)
        let response: APIScraperConfigResponse = try await client.request(
            APIEndpoints.scraperConfig(id: configId),
            method: .put,
            body: body
        )
        return response
    }

    func deleteConfig(configId: Int) async throws {
        try await client.requestVoid(APIEndpoints.scraperConfig(id: configId), method: .delete)
    }

    /// Subscribe to a detected feed.
    func subscribeFeed(
        feedURL: String,
        feedType: String,
        displayName: String?
    ) async throws -> ScraperConfig {
        let payload = APISubscribeToFeedRequest(
            feedUrl: feedURL,
            feedType: feedType,
            displayName: displayName
        )
        let body = try JSONEncoder().encode(payload)
        let response: APIScraperConfigResponse = try await client.request(
            APIEndpoints.subscribeFeed,
            method: .post,
            body: body
        )
        return response
    }

    private func scraperConfig(feedURL: String?, limit: Int?) -> [String: AnyCodable] {
        var config: [String: AnyCodable] = [:]
        if let feedURL {
            config["feed_url"] = AnyCodable(feedURL)
        }
        if let limit {
            config["limit"] = AnyCodable(limit)
        }
        return config
    }
}
