import Foundation
import OSLog

private let briefingServiceLogger = Logger(subsystem: "com.newsly", category: "BriefingRead")

enum BriefingIndexFetchResult {
    case value(APIBriefingIndexResponse, etag: String?)
    case notModified
}

enum BriefingLensFetchError: LocalizedError {
    case staleCursor

    var errorDescription: String? {
        "This Briefing changed while loading. Retry to reload the Lens from the beginning."
    }
}

protocol BriefingServicing: AnyObject {
    func fetchIndex(ifNoneMatch etag: String?) async throws -> BriefingIndexFetchResult
    func fetchLens(
        key: String,
        limit: Int?,
        cursor: String?
    ) async throws -> APIBriefingLensResponse
    func markRead(sourceKeys: [String]) async throws -> APIBriefingReadMarkResponse
    func markLensRead(key: String) async throws -> APIBriefingReadMarkResponse
    func requestRefresh() async throws -> APIBriefingRefreshResponse
    func completeFirstRun() async throws
    func digSearch(fragment: String) async throws -> APIBriefingDigSearchResponse
    func digSummarize(
        fragment: String,
        passageContext: String,
        results: [APIBriefingDigSearchResult]
    ) async throws -> APIBriefingDigSummarizeResponse
    func requestNarration(programKey: String) async throws -> BriefingNarration
    func fetchNarration(episodeGroupID: String) async throws -> BriefingNarration
}

final class LiveBriefingService: BriefingServicing {
    typealias CompleteFirstRun = () async throws -> Void

    private let apiClient: APIClient
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder
    private let completeFirstRunAction: CompleteFirstRun

    init(
        apiClient: APIClient,
        decoder: JSONDecoder = JSONDecoder(),
        encoder: JSONEncoder = JSONEncoder(),
        completeFirstRun: @escaping CompleteFirstRun
    ) {
        self.apiClient = apiClient
        self.decoder = decoder
        self.encoder = encoder
        self.completeFirstRunAction = completeFirstRun
    }

    func fetchIndex(ifNoneMatch etag: String?) async throws -> BriefingIndexFetchResult {
        let signpostState = BriefingPerformance.signposter.beginInterval("index-fetch-decode")
        defer { BriefingPerformance.signposter.endInterval("index-fetch-decode", signpostState) }
        var headers: [String: String] = [:]
        if let etag {
            headers["If-None-Match"] = etag
        }
        let networkStartedAt = Date()
        let (data, response) = try await apiClient.requestHTTP(
            APIEndpoints.briefing,
            headers: headers.isEmpty ? nil : headers,
            allowedStatusCodes: [304],
            recoveryPolicy: .safeRead
        )
        if response.statusCode == 304 {
            briefingServiceLogger.info(
                "Index unchanged | network_ms=\(Int(Date().timeIntervalSince(networkStartedAt) * 1_000), privacy: .public) bytes=\(data.count, privacy: .public)"
            )
            return .notModified
        }
        let decodeStartedAt = Date()
        let index = try decoder.decode(APIBriefingIndexResponse.self, from: data)
        briefingServiceLogger.info(
            "Index decoded | network_ms=\(Int(decodeStartedAt.timeIntervalSince(networkStartedAt) * 1_000), privacy: .public) decode_ms=\(Int(Date().timeIntervalSince(decodeStartedAt) * 1_000), privacy: .public) bytes=\(data.count, privacy: .public) version=\(index.version, privacy: .public) lenses=\(index.lenses.count, privacy: .public)"
        )
        return .value(index, etag: response.value(forHTTPHeaderField: "ETag"))
    }

    func fetchLens(
        key: String,
        limit: Int?,
        cursor: String?
    ) async throws -> APIBriefingLensResponse {
        let signpostState = BriefingPerformance.signposter.beginInterval("lens-page-fetch-decode")
        defer {
            BriefingPerformance.signposter.endInterval("lens-page-fetch-decode", signpostState)
        }
        let encodedKey = key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key
        var queryItems: [URLQueryItem] = []
        if let limit {
            queryItems.append(URLQueryItem(name: "limit", value: String(limit)))
        }
        if let cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }
        let networkStartedAt = Date()
        let (data, response) = try await apiClient.requestHTTP(
            APIEndpoints.briefingLens(encodedKey),
            queryItems: queryItems.isEmpty ? nil : queryItems,
            allowedStatusCodes: [409],
            recoveryPolicy: .safeRead
        )
        if response.statusCode == 409 {
            throw BriefingLensFetchError.staleCursor
        }
        let decodeStartedAt = Date()
        do {
            let lens = try decoder.decode(APIBriefingLensResponse.self, from: data)
            briefingServiceLogger.info(
                "Lens decoded | key=\(key, privacy: .public) network_ms=\(Int(decodeStartedAt.timeIntervalSince(networkStartedAt) * 1_000), privacy: .public) decode_ms=\(Int(Date().timeIntervalSince(decodeStartedAt) * 1_000), privacy: .public) bytes=\(data.count, privacy: .public) version=\(lens.version, privacy: .public) segments=\(lens.segments.count, privacy: .public) sources=\(lens.sources.count, privacy: .public) continuation=\(cursor != nil, privacy: .public)"
            )
            return lens
        } catch {
            throw ClientFailure.decoding(endpoint: APIEndpoints.briefingLens(encodedKey))
        }
    }

    func markRead(sourceKeys: [String]) async throws -> APIBriefingReadMarkResponse {
        let body = try encoder.encode(APIBriefingReadMarkRequest(sourceKeys: sourceKeys))
        return try await apiClient.request(
            APIEndpoints.briefingReadMarks,
            method: .post,
            body: body
        )
    }

    func markLensRead(key: String) async throws -> APIBriefingReadMarkResponse {
        let encodedKey = key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key
        return try await apiClient.request(
            APIEndpoints.briefingLensReadMarks(encodedKey),
            method: .post
        )
    }

    func requestRefresh() async throws -> APIBriefingRefreshResponse {
        try await apiClient.request(APIEndpoints.briefingRefresh, method: .post)
    }

    func completeFirstRun() async throws {
        try await completeFirstRunAction()
    }

    func digSearch(fragment: String) async throws -> APIBriefingDigSearchResponse {
        let body = try encoder.encode(APIBriefingDigSearchRequest(fragment: fragment))
        return try await apiClient.request(
            APIEndpoints.briefingDigSearch,
            method: .post,
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
            method: .post,
            body: body
        )
    }

    func requestNarration(programKey: String) async throws -> BriefingNarration {
        let scope = BriefingNarrationProgram.scope(for: programKey)
        let body = try encoder.encode(
            APIBriefingNarrationRequest(
                scope: scope,
                lensKey: scope == nil ? programKey : nil
            )
        )
        return try await apiClient.request(
            APIEndpoints.briefingNarration,
            method: .post,
            body: body
        )
    }

    func fetchNarration(episodeGroupID: String) async throws -> BriefingNarration {
        try await apiClient.request(
            APIEndpoints.briefingNarration(episodeGroupID: episodeGroupID)
        )
    }
}
