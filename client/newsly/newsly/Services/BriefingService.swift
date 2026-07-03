import Foundation

enum BriefingIndexFetchResult {
    case value(APIBriefingIndexResponse, etag: String?)
    case notModified
}

protocol BriefingServicing: AnyObject {
    func fetchIndex(ifNoneMatch etag: String?) async throws -> BriefingIndexFetchResult
    func fetchLens(key: String) async throws -> APIBriefingLensResponse
    func markRead(sourceKeys: [String]) async throws -> APIBriefingReadMarkResponse
    func requestRefresh() async throws -> APIBriefingRefreshResponse
    func digSearch(fragment: String) async throws -> APIBriefingDigSearchResponse
    func digSummarize(
        fragment: String,
        passageContext: String,
        results: [APIBriefingDigSearchResult]
    ) async throws -> APIBriefingDigSummarizeResponse
    func requestNarration(lensKey: String) async throws -> AudioEpisode
}

final class LiveBriefingService: BriefingServicing {
    private let apiClient: APIClient
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    init(
        apiClient: APIClient = .shared,
        decoder: JSONDecoder = JSONDecoder(),
        encoder: JSONEncoder = JSONEncoder()
    ) {
        self.apiClient = apiClient
        self.decoder = decoder
        self.encoder = encoder
    }

    func fetchIndex(ifNoneMatch etag: String?) async throws -> BriefingIndexFetchResult {
        var headers: [String: String] = [:]
        if let etag {
            headers["If-None-Match"] = etag
        }
        let (data, response) = try await apiClient.requestHTTP(
            APIEndpoints.briefing,
            additionalHeaders: headers.isEmpty ? nil : headers,
            additionalAllowedStatusCodes: [304]
        )
        if response.statusCode == 304 {
            return .notModified
        }
        let index = try decoder.decode(APIBriefingIndexResponse.self, from: data)
        return .value(index, etag: response.value(forHTTPHeaderField: "ETag"))
    }

    func fetchLens(key: String) async throws -> APIBriefingLensResponse {
        let encodedKey = key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key
        return try await apiClient.request(APIEndpoints.briefingLens(encodedKey))
    }

    func markRead(sourceKeys: [String]) async throws -> APIBriefingReadMarkResponse {
        let body = try encoder.encode(APIBriefingReadMarkRequest(sourceKeys: sourceKeys))
        return try await apiClient.request(
            APIEndpoints.briefingReadMarks,
            method: "POST",
            body: body
        )
    }

    func requestRefresh() async throws -> APIBriefingRefreshResponse {
        try await apiClient.request(APIEndpoints.briefingRefresh, method: "POST")
    }

    func digSearch(fragment: String) async throws -> APIBriefingDigSearchResponse {
        let body = try encoder.encode(APIBriefingDigSearchRequest(fragment: fragment))
        return try await apiClient.request(
            APIEndpoints.briefingDigSearch,
            method: "POST",
            body: body
        )
    }

    func digSummarize(
        fragment: String,
        passageContext: String,
        results: [APIBriefingDigSearchResult]
    ) async throws -> APIBriefingDigSummarizeResponse {
        let body = try encoder.encode(
            APIBriefingDigSummarizeRequest(
                fragment: fragment,
                passageContext: passageContext,
                results: results
            )
        )
        return try await apiClient.request(
            APIEndpoints.briefingDigSummarize,
            method: "POST",
            body: body
        )
    }

    func requestNarration(lensKey: String) async throws -> AudioEpisode {
        let body = try encoder.encode(APIBriefingNarrationRequest(lensKey: lensKey))
        return try await apiClient.request(
            APIEndpoints.briefingNarration,
            method: "POST",
            body: body
        )
    }
}
