//
//  APIClient.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Combine
import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "APIClient")

enum APIError: LocalizedError {
    case invalidURL
    case noData
    case decodingError(Error)
    case networkError(Error)
    case httpError(statusCode: Int)
    case unauthorized
    case unknown

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid URL"
        case .noData:
            return "No data received"
        case .decodingError(let error):
            return "Failed to decode response: \(error.localizedDescription)"
        case .networkError(let error):
            return "Network error: \(error.localizedDescription)"
        case .httpError(let statusCode):
            return "HTTP error: \(statusCode)"
        case .unauthorized:
            return "Unauthorized - please sign in again"
        case .unknown:
            return "An unknown error occurred"
        }
    }
}

struct APIRequestDescriptor<Response: Decodable> {
    let path: String
    let method: String
    let body: Data?
    let queryItems: [URLQueryItem]?

    init(
        path: String,
        method: String = "GET",
        body: Data? = nil,
        queryItems: [URLQueryItem]? = nil
    ) {
        self.path = path
        self.method = method
        self.body = body
        self.queryItems = queryItems
    }
}

class APIClient {
    static let shared = APIClient()
    private let session: URLSession
    private let decoder: JSONDecoder
    private let tokenStore: AuthTokenStore
    private let tokenRefresher: TokenRefreshing

    init(
        session: URLSession = .shared,
        decoder: JSONDecoder = JSONDecoder(),
        tokenStore: AuthTokenStore = KeychainManager.shared,
        tokenRefresher: TokenRefreshing = TokenRefreshService.shared
    ) {
        self.session = session
        self.decoder = decoder
        self.tokenStore = tokenStore
        self.tokenRefresher = tokenRefresher
    }

    func request<T: Decodable>(
        _ descriptor: APIRequestDescriptor<T>,
        allowRefresh: Bool = true
    ) async throws -> T {
        try await request(
            descriptor.path,
            method: descriptor.method,
            body: descriptor.body,
            queryItems: descriptor.queryItems,
            allowRefresh: allowRefresh
        )
    }
    
    func request<T: Decodable>(_ endpoint: String,
                               method: String = "GET",
                               body: Data? = nil,
                               queryItems: [URLQueryItem]? = nil,
                               allowRefresh: Bool = true) async throws -> T {
        let (data, _) = try await executeRequest(
            endpoint: endpoint,
            method: method,
            body: body,
            queryItems: queryItems,
            accept: nil,
            allowRefresh: allowRefresh,
            authFailureReason: "request_no_refresh_remaining"
        )

        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decodingError(error)
        }
    }

    func requestData(
        _ endpoint: String,
        method: String = "GET",
        body: Data? = nil,
        queryItems: [URLQueryItem]? = nil,
        accept: String? = nil,
        allowRefresh: Bool = true
    ) async throws -> Data {
        let (data, _) = try await executeRequest(
            endpoint: endpoint,
            method: method,
            body: body,
            queryItems: queryItems,
            accept: accept,
            allowRefresh: allowRefresh,
            authFailureReason: "request_data_no_refresh_remaining"
        )
        return data
    }
    
    func requestVoid(_ endpoint: String,
                     method: String = "POST",
                     body: Data? = nil,
                     allowRefresh: Bool = true) async throws {
        _ = try await executeRequest(
            endpoint: endpoint,
            method: method,
            body: body,
            queryItems: nil,
            accept: nil,
            allowRefresh: allowRefresh,
            authFailureReason: "request_void_no_refresh_remaining"
        )
    }
    
    func requestRaw(_ endpoint: String,
                    method: String = "GET",
                    body: Data? = nil,
                    queryItems: [URLQueryItem]? = nil,
                    allowRefresh: Bool = true) async throws -> [String: Any] {
        let (data, _) = try await executeRequest(
            endpoint: endpoint,
            method: method,
            body: body,
            queryItems: queryItems,
            accept: nil,
            allowRefresh: allowRefresh,
            authFailureReason: "request_raw_no_refresh_remaining"
        )

        guard let json = try JSONSerialization.jsonObject(with: data, options: []) as? [String: Any] else {
            throw APIError.decodingError(
                NSError(
                    domain: "APIClient",
                    code: 0,
                    userInfo: [NSLocalizedDescriptionKey: "Invalid JSON response"]
                )
            )
        }
        return json
    }

    private func buildRequest(
        endpoint: String,
        method: String,
        body: Data?,
        queryItems: [URLQueryItem]?,
        accept: String?
    ) async throws -> (request: URLRequest, sentAuthHeader: Bool) {
        guard var components = URLComponents(string: AppSettings.shared.baseURL + endpoint) else {
            throw APIError.invalidURL
        }
        if let queryItems {
            components.queryItems = queryItems
        }
        guard let url = components.url else {
            throw APIError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let accept {
            request.setValue(accept, forHTTPHeaderField: "Accept")
        }

        if let accessToken = try await fetchAccessTokenOrRefresh(endpoint: endpoint) {
            request.addValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.httpBody = body
        }
        let sentAuthHeader = request.value(forHTTPHeaderField: "Authorization") != nil
        return (request, sentAuthHeader)
    }

    private func executeRequest(
        endpoint: String,
        method: String,
        body: Data?,
        queryItems: [URLQueryItem]?,
        accept: String?,
        allowRefresh: Bool,
        authFailureReason: String
    ) async throws -> (Data, HTTPURLResponse) {
        let (request, sentAuthHeader) = try await buildRequest(
            endpoint: endpoint,
            method: method,
            body: body,
            queryItems: queryItems,
            accept: accept
        )

        do {
            let (data, response) = try await session.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse else {
                throw APIError.unknown
            }

            if httpResponse.statusCode == 401 || httpResponse.statusCode == 403 {
                let detail = extractErrorDetail(from: data)
                guard shouldTreatAsAuthFailure(
                    statusCode: httpResponse.statusCode,
                    response: httpResponse,
                    detail: detail,
                    sentAuthHeader: sentAuthHeader
                ) else {
                    logger.error(
                        "[APIClient] Non-auth HTTP error | endpoint=\(endpoint, privacy: .public) status=\(httpResponse.statusCode) detail=\((detail ?? "n/a"), privacy: .public)"
                    )
                    throw APIError.httpError(statusCode: httpResponse.statusCode)
                }

                guard allowRefresh else {
                    notifyAuthenticationRequired(
                        endpoint: endpoint,
                        statusCode: httpResponse.statusCode,
                        detail: detail,
                        sentAuthHeader: sentAuthHeader,
                        reason: authFailureReason
                    )
                    throw APIError.unauthorized
                }

                do {
                    _ = try await tokenRefresher.refreshAccessToken()
                    return try await executeRequest(
                        endpoint: endpoint,
                        method: method,
                        body: body,
                        queryItems: queryItems,
                        accept: accept,
                        allowRefresh: false,
                        authFailureReason: authFailureReason
                    )
                } catch let authError as AuthError {
                    switch authError {
                    case .refreshTokenExpired, .noRefreshToken:
                        notifyAuthenticationRequired(
                            endpoint: endpoint,
                            statusCode: httpResponse.statusCode,
                            detail: detail,
                            sentAuthHeader: sentAuthHeader,
                            reason: "refresh_token_unavailable_or_expired"
                        )
                        notifyAuthDidLogOut()
                        throw APIError.unauthorized
                    default:
                        throw APIError.networkError(authError)
                    }
                } catch {
                    throw APIError.networkError(error)
                }
            }

            guard (200...299).contains(httpResponse.statusCode) else {
                throw APIError.httpError(statusCode: httpResponse.statusCode)
            }

            return (data, httpResponse)
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.networkError(error)
        }
    }

    /// Stream NDJSON responses line by line
    func streamNDJSON<T: Decodable>(
        _ endpoint: String,
        method: String = "POST",
        body: Data? = nil
    ) -> AsyncThrowingStream<T, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    guard let url = URL(string: AppSettings.shared.baseURL + endpoint) else {
                        logger.error("[Stream] Invalid URL | endpoint=\(endpoint, privacy: .public)")
                        continuation.finish(throwing: APIError.invalidURL)
                        return
                    }

                    var request = URLRequest(url: url)
                    request.httpMethod = method
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.setValue("application/x-ndjson", forHTTPHeaderField: "Accept")

                    if let accessToken = try await fetchAccessTokenOrRefresh(endpoint: endpoint) {
                        request.addValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
                    }
                    let sentAuthHeader = request.value(forHTTPHeaderField: "Authorization") != nil

                    if let body = body {
                        request.httpBody = body
                    }

                    logger.info("[Stream] Starting request | endpoint=\(endpoint, privacy: .public)")
                    let (bytes, response) = try await session.bytes(for: request)

                    guard let httpResponse = response as? HTTPURLResponse else {
                        logger.error("[Stream] No HTTP response")
                        continuation.finish(throwing: APIError.unknown)
                        return
                    }

                    logger.info("[Stream] Got response | status=\(httpResponse.statusCode) headers=\(httpResponse.allHeaderFields.count)")

                    guard (200...299).contains(httpResponse.statusCode) else {
                        if httpResponse.statusCode == 401 || httpResponse.statusCode == 403 {
                            let detail = extractErrorDetail(from: Data())
                            if shouldTreatAsAuthFailure(
                                statusCode: httpResponse.statusCode,
                                response: httpResponse,
                                detail: detail,
                                sentAuthHeader: sentAuthHeader
                            ) {
                                notifyAuthenticationRequired(
                                    endpoint: endpoint,
                                    statusCode: httpResponse.statusCode,
                                    detail: detail,
                                    sentAuthHeader: sentAuthHeader,
                                    reason: "stream_auth_failure"
                                )
                                logger.error("[Stream] Auth error | endpoint=\(endpoint, privacy: .public) status=\(httpResponse.statusCode)")
                                continuation.finish(throwing: APIError.unauthorized)
                            } else {
                                logger.error(
                                    "[Stream] Non-auth HTTP error | endpoint=\(endpoint, privacy: .public) status=\(httpResponse.statusCode)"
                                )
                                continuation.finish(throwing: APIError.httpError(statusCode: httpResponse.statusCode))
                            }
                        } else {
                            logger.error("[Stream] HTTP error | endpoint=\(endpoint, privacy: .public) status=\(httpResponse.statusCode)")
                            continuation.finish(throwing: APIError.httpError(statusCode: httpResponse.statusCode))
                        }
                        return
                    }

                    logger.info("[Stream] Starting to read lines")
                    var lineCount = 0
                    for try await line in bytes.lines {
                        lineCount += 1
                        logger.debug("[Stream] Got line \(lineCount) | length=\(line.count)")
                        guard !line.isEmpty else { continue }

                        guard let lineData = line.data(using: .utf8) else {
                            logger.error("[Stream] Failed to convert line to data: \(line, privacy: .public)")
                            continue
                        }

                        do {
                            let decoded = try self.decoder.decode(T.self, from: lineData)
                            continuation.yield(decoded)
                        } catch {
                            // Log the actual line content to diagnose decode issues
                            logger.error("[Stream] Decode error: \(error.localizedDescription, privacy: .public)")
                            logger.error("[Stream] Failed line content: \(line.prefix(500), privacy: .public)")
                        }
                    }

                    logger.info("[Stream] Finished reading | total lines=\(lineCount)")
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch {
                    logger.error("[Stream] Error | endpoint=\(endpoint, privacy: .public) error=\(error.localizedDescription, privacy: .public)")
                    continuation.finish(throwing: error)
                }
            }
        }
    }

    /// Get an access token if present; otherwise attempt a refresh.
    /// Returns nil for truly unauthenticated flows (e.g., public endpoints).
    private func fetchAccessTokenOrRefresh(endpoint: String) async throws -> String? {
        if let token = tokenStore.getToken(key: .accessToken) {
            return token
        }

        // If we have a refresh token, attempt to refresh and return the new access token.
        guard tokenStore.getToken(key: .refreshToken) != nil else {
            return nil
        }

        do {
            let refreshed = try await tokenRefresher.refreshAccessToken()
            return refreshed
        } catch let authError as AuthError {
            switch authError {
            case .refreshTokenExpired, .noRefreshToken:
                notifyAuthenticationRequired(
                    endpoint: endpoint,
                    statusCode: 401,
                    detail: authError.localizedDescription,
                    sentAuthHeader: false,
                    reason: "fetch_token_refresh_failed"
                )
                notifyAuthDidLogOut()
                throw APIError.unauthorized
            default:
                throw APIError.networkError(authError)
            }
        } catch {
            throw APIError.networkError(error)
        }
    }

    private func shouldTreatAsAuthFailure(
        statusCode: Int,
        response: HTTPURLResponse,
        detail: String?,
        sentAuthHeader: Bool
    ) -> Bool {
        if statusCode == 401 {
            return true
        }
        guard statusCode == 403 else {
            return false
        }

        // Missing auth header on a protected endpoint is authentication failure.
        if !sentAuthHeader {
            return true
        }

        if let wwwAuth = response.value(forHTTPHeaderField: "WWW-Authenticate")?.lowercased(),
           wwwAuth.contains("bearer") {
            return true
        }

        guard let lowered = detail?.lowercased() else {
            return false
        }

        let authMarkers = [
            "not authenticated",
            "could not validate credentials",
            "invalid token",
            "token expired",
            "expired token",
            "missing token",
            "invalid refresh token",
            "unauthorized"
        ]
        return authMarkers.contains { lowered.contains($0) }
    }

    private func extractErrorDetail(from data: Data) -> String? {
        guard !data.isEmpty else {
            return nil
        }

        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let detail = json["detail"] {
            return String(describing: detail).prefix(240).description
        }

        if let raw = String(data: data, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines),
           !raw.isEmpty {
            return raw.prefix(240).description
        }

        return nil
    }

    private func notifyAuthenticationRequired(
        endpoint: String,
        statusCode: Int?,
        detail: String?,
        sentAuthHeader: Bool,
        reason: String
    ) {
        let hasAccessToken = tokenStore.getToken(key: .accessToken) != nil
        let hasRefreshToken = tokenStore.getToken(key: .refreshToken) != nil
        let statusText = statusCode.map(String.init) ?? "n/a"
        let detailText = detail ?? "n/a"

        logger.error(
            "[Auth] Authentication required | endpoint=\(endpoint, privacy: .public) reason=\(reason, privacy: .public) status=\(statusText, privacy: .public) sentAuth=\(sentAuthHeader) hasAccess=\(hasAccessToken) hasRefresh=\(hasRefreshToken) detail=\(detailText, privacy: .public)"
        )

        var userInfo: [String: Any] = [
            "endpoint": endpoint,
            "reason": reason,
            "sentAuthHeader": sentAuthHeader,
            "hasAccessToken": hasAccessToken,
            "hasRefreshToken": hasRefreshToken
        ]
        if let statusCode {
            userInfo["statusCode"] = statusCode
        }
        if let detail {
            userInfo["detail"] = detail
        }

        NotificationCenter.default.post(name: .authenticationRequired, object: nil, userInfo: userInfo)
    }

    private func notifyAuthDidLogOut() {
        NotificationCenter.default.post(name: .authDidLogOut, object: nil)
    }
}

// MARK: - Notification Extensions

extension Notification.Name {
    static let authenticationRequired = Notification.Name("authenticationRequired")
    static let authDidLogOut = Notification.Name("authDidLogOut")
}

// MARK: - Combine bridge

extension APIClient {
    func publisher<T: Decodable>(
        _ endpoint: String,
        method: String = "GET",
        body: Data? = nil,
        queryItems: [URLQueryItem]? = nil
    ) -> AnyPublisher<T, Error> {
        Deferred {
            Future { promise in
                Task {
                    do {
                        let result: T = try await self.request(
                            endpoint,
                            method: method,
                            body: body,
                            queryItems: queryItems
                        )
                        promise(.success(result))
                    } catch {
                        promise(.failure(error))
                    }
                }
            }
        }
        .eraseToAnyPublisher()
    }

    func publisherVoid(
        _ endpoint: String,
        method: String = "POST",
        body: Data? = nil
    ) -> AnyPublisher<Void, Error> {
        Deferred {
            Future { promise in
                Task {
                    do {
                        try await self.requestVoid(endpoint, method: method, body: body)
                        promise(.success(()))
                    } catch {
                        promise(.failure(error))
                    }
                }
            }
        }
        .eraseToAnyPublisher()
    }
}
