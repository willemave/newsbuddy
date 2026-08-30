//
//  ScraperConfigService.swift
//  newsly
//

import Foundation

struct CreateScraperConfigPayload: Codable {
    let scraperType: String
    let displayName: String?
    let config: ScraperConfigBody
    let isActive: Bool

    enum CodingKeys: String, CodingKey {
        case scraperType = "scraper_type"
        case displayName = "display_name"
        case config
        case isActive = "is_active"
    }
}

struct UpdateScraperConfigPayload: Codable {
    let displayName: String?
    let config: ScraperConfigBody?
    let isActive: Bool?

    enum CodingKeys: String, CodingKey {
        case displayName = "display_name"
        case config
        case isActive = "is_active"
    }
}

struct ScraperConfigBody: Codable {
    let feedURL: String?
    let limit: Int?

    enum CodingKeys: String, CodingKey {
        case feedURL = "feed_url"
        case limit
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
        let payload = CreateScraperConfigPayload(
            scraperType: scraperType,
            displayName: displayName,
            config: ScraperConfigBody(feedURL: feedURL, limit: limit),
            isActive: isActive
        )
        let body = try JSONEncoder().encode(payload)
        return try await client.request(APIEndpoints.scraperConfigs, method: .post, body: body)
    }

    func updateConfig(
        configId: Int,
        displayName: String?,
        feedURL: String?,
        limit: Int?,
        isActive: Bool?
    ) async throws -> ScraperConfig {
        let configBody = (feedURL != nil || limit != nil) ? ScraperConfigBody(feedURL: feedURL, limit: limit) : nil
        let payload = UpdateScraperConfigPayload(displayName: displayName, config: configBody, isActive: isActive)
        let body = try JSONEncoder().encode(payload)
        return try await client.request(APIEndpoints.scraperConfig(id: configId), method: .put, body: body)
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
        let payload = SubscribeFeedPayload(
            feedURL: feedURL,
            feedType: feedType,
            displayName: displayName
        )
        let body = try JSONEncoder().encode(payload)
        return try await client.request(APIEndpoints.subscribeFeed, method: .post, body: body)
    }
}

struct SubscribeFeedPayload: Codable {
    let feedURL: String
    let feedType: String
    let displayName: String?

    enum CodingKeys: String, CodingKey {
        case feedURL = "feed_url"
        case feedType = "feed_type"
        case displayName = "display_name"
    }
}
