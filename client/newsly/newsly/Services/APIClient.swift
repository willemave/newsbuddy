//
//  APIClient.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "APIClient")

final class APIClient {
    private let transport: HTTPTransport
    private let baseURLProvider: () -> URL?
    private let decoder: JSONDecoder
    private let credentialSession: any CredentialSessionProviding

    init(
        transport: HTTPTransport,
        baseURLProvider: @escaping () -> URL?,
        decoder: JSONDecoder = JSONDecoder(),
        credentialSession: any CredentialSessionProviding
    ) {
        self.transport = transport
        self.baseURLProvider = baseURLProvider
        self.decoder = decoder
        self.credentialSession = credentialSession
    }

    convenience init(
        session: URLSession,
        baseURLProvider: @escaping () -> URL?,
        decoder: JSONDecoder = JSONDecoder(),
        credentialSession: any CredentialSessionProviding
    ) {
        self.init(
            transport: HTTPTransport(session: session),
            baseURLProvider: baseURLProvider,
            decoder: decoder,
            credentialSession: credentialSession
        )
    }

    func request<T: Decodable>(
        _ endpoint: String,
        method: HTTPMethod = .get,
        body: Data? = nil,
        queryItems: [URLQueryItem]? = nil,
        headers: [String: String]? = nil,
        allowedStatusCodes: Set<Int> = [],
        recoveryPolicy: RequestRecoveryPolicy = .none,
        authentication: RequestAuthentication = .required,
        decoding: ResponseDecoding = .standard
    ) async throws -> T {
        let (data, _) = try await executeRequest(
            endpoint: endpoint,
            method: method,
            body: body,
            queryItems: queryItems,
            accept: nil,
            additionalHeaders: headers,
            additionalAllowedStatusCodes: allowedStatusCodes,
            allowRefresh: true,
            authentication: authentication,
            recoveryBudget: RequestRecoveryBudget(policy: recoveryPolicy)
        )
        return try decodeResponse(data, endpoint: endpoint, decoding: decoding)
    }

    private func decodeResponse<T: Decodable>(
        _ data: Data,
        endpoint: String,
        decoding: ResponseDecoding
    ) throws -> T {
        do {
            switch decoding {
            case .standard:
                return try decoder.decode(T.self, from: data)
            case .iso8601:
                let decoder = JSONDecoder()
                decoder.dateDecodingStrategy = .iso8601
                return try decoder.decode(T.self, from: data)
            }
        } catch {
            throw ClientFailure.decoding(endpoint: endpoint)
        }
    }

    func requestHTTP(
        _ endpoint: String,
        method: HTTPMethod = .get,
        body: Data? = nil,
        queryItems: [URLQueryItem]? = nil,
        accept: String? = nil,
        headers: [String: String]? = nil,
        allowedStatusCodes: Set<Int> = [],
        recoveryPolicy: RequestRecoveryPolicy = .none,
        authentication: RequestAuthentication = .required
    ) async throws -> (Data, HTTPURLResponse) {
        try await executeRequest(
            endpoint: endpoint,
            method: method,
            body: body,
            queryItems: queryItems,
            accept: accept,
            additionalHeaders: headers,
            additionalAllowedStatusCodes: allowedStatusCodes,
            allowRefresh: true,
            authentication: authentication,
            recoveryBudget: RequestRecoveryBudget(policy: recoveryPolicy)
        )
    }

    func authorizedMediaResource(
        _ endpoint: String,
        accept: String? = nil
    ) async throws -> AuthorizedMediaResource {
        let (request, _) = try await buildRequest(
            endpoint: endpoint,
            method: .get,
            body: nil,
            queryItems: nil,
            accept: accept,
            additionalHeaders: nil,
            authentication: .required
        )
        guard let url = request.url else {
            throw ClientFailure.invalidRequest
        }
        var headers = request.allHTTPHeaderFields ?? [:]
        headers.removeValue(forKey: "Content-Type")
        return AuthorizedMediaResource(
            url: url,
            headers: headers
        )
    }
    
    func requestVoid(
        _ endpoint: String,
        method: HTTPMethod = .post,
        body: Data? = nil,
        queryItems: [URLQueryItem]? = nil,
        headers: [String: String]? = nil,
        allowedStatusCodes: Set<Int> = [],
        recoveryPolicy: RequestRecoveryPolicy = .none,
        authentication: RequestAuthentication = .required
    ) async throws {
        _ = try await executeRequest(
            endpoint: endpoint,
            method: method,
            body: body,
            queryItems: queryItems,
            accept: nil,
            additionalHeaders: headers,
            additionalAllowedStatusCodes: allowedStatusCodes,
            allowRefresh: true,
            authentication: authentication,
            recoveryBudget: RequestRecoveryBudget(policy: recoveryPolicy)
        )
    }
    
    private func buildRequest(
        endpoint: String,
        method: HTTPMethod,
        body: Data?,
        queryItems: [URLQueryItem]?,
        accept: String?,
        additionalHeaders: [String: String]?,
        authentication: RequestAuthentication
    ) async throws -> (request: URLRequest, sentAuthHeader: Bool) {
        guard let baseURL = baseURLProvider(),
              let endpointURL = URL(string: endpoint, relativeTo: baseURL)?.absoluteURL,
              var components = URLComponents(url: endpointURL, resolvingAgainstBaseURL: false) else {
            throw ClientFailure.invalidRequest
        }
        if let queryItems {
            components.queryItems = queryItems
        }
        guard let url = components.url else {
            throw ClientFailure.invalidRequest
        }

        var request = URLRequest(url: url)
        request.httpMethod = method.rawValue
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        for (field, value) in Self.clientTelemetryHeaders() {
            request.setValue(value, forHTTPHeaderField: field)
        }
        if let accept {
            request.setValue(accept, forHTTPHeaderField: "Accept")
        }
        for (field, value) in additionalHeaders ?? [:] {
            request.setValue(value, forHTTPHeaderField: field)
        }

        do {
            if let accessToken = try await credentialSession.accessToken(for: authentication) {
                request.addValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
            }
        } catch {
            throw ClientFailure.classify(error)
        }
        if let body {
            request.httpBody = body
        }
        let sentAuthHeader = request.value(forHTTPHeaderField: "Authorization") != nil
        return (request, sentAuthHeader)
    }

    private static func clientTelemetryHeaders(bundle: Bundle = .main) -> [String: String] {
        let client = bundle.bundleURL.pathExtension.lowercased() == "appex"
            ? "ios_share_extension"
            : "ios"
        var headers = ["X-Newsly-Client": client]
        if let version = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
           !version.isEmpty {
            headers["X-Newsly-Client-Version"] = version
        }
        if let build = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String,
           !build.isEmpty {
            headers["X-Newsly-Client-Build"] = build
        }
        return headers
    }

    private func executeRequest(
        endpoint: String,
        method: HTTPMethod,
        body: Data?,
        queryItems: [URLQueryItem]?,
        accept: String?,
        additionalHeaders: [String: String]? = nil,
        additionalAllowedStatusCodes: Set<Int> = [],
        allowRefresh: Bool,
        authentication: RequestAuthentication,
        recoveryBudget: RequestRecoveryBudget
    ) async throws -> (Data, HTTPURLResponse) {
        do {
            let (request, sentAuthHeader) = try await buildRequest(
                endpoint: endpoint,
                method: method,
                body: body,
                queryItems: queryItems,
                accept: accept,
                additionalHeaders: additionalHeaders,
                authentication: authentication
            )
            let (data, httpResponse) = try await sendResourceRequest(
                request,
                endpoint: endpoint,
                method: method,
                recoveryBudget: recoveryBudget
            )

            let canRecoverAuthentication = authentication == .required || sentAuthHeader
            if canRecoverAuthentication,
               httpResponse.statusCode == 401 || httpResponse.statusCode == 403 {
                let errorResponse = HTTPResponseDetail.errorResponse(from: data)
                let detail = HTTPResponseDetail.extract(from: data)
                guard shouldTreatAsAuthFailure(
                    statusCode: httpResponse.statusCode,
                    response: httpResponse,
                    errorCode: errorResponse?.code,
                    sentAuthHeader: sentAuthHeader
                ) else {
                    logger.error(
                        "[APIClient] Non-auth HTTP error | endpoint=\(endpoint, privacy: .public) status=\(httpResponse.statusCode) detail=\((detail ?? "n/a"), privacy: .public)"
                    )
                    throw HTTPResponseDetail.failure(
                        statusCode: httpResponse.statusCode,
                        data: data
                    )
                }

                guard allowRefresh else {
                    throw ClientFailure.authenticationExpired
                }

                let rejectedAccessToken = request.value(
                    forHTTPHeaderField: "Authorization"
                )?.replacingOccurrences(of: "Bearer ", with: "")
                _ = try await credentialSession.refreshAfterRejection(
                    rejectedAccessToken: rejectedAccessToken
                )
                return try await executeRequest(
                    endpoint: endpoint,
                    method: method,
                    body: body,
                    queryItems: queryItems,
                    accept: accept,
                    additionalHeaders: additionalHeaders,
                    additionalAllowedStatusCodes: additionalAllowedStatusCodes,
                    allowRefresh: false,
                    authentication: authentication,
                    recoveryBudget: recoveryBudget
                )
            }

            guard (200...299).contains(httpResponse.statusCode)
                || additionalAllowedStatusCodes.contains(httpResponse.statusCode)
            else {
                throw HTTPResponseDetail.failure(
                    statusCode: httpResponse.statusCode,
                    data: data
                )
            }

            return (data, httpResponse)
        } catch {
            throw ClientFailure.classify(error)
        }
    }

    private func sendResourceRequest(
        _ request: URLRequest,
        endpoint: String,
        method: HTTPMethod,
        recoveryBudget: RequestRecoveryBudget
    ) async throws -> (Data, HTTPURLResponse) {
        while true {
            do {
                return try await transport.send(request)
            } catch HTTPTransportError.invalidResponse {
                throw ClientFailure.invalidResponse
            } catch {
                let failure = ClientFailure.classify(error)
                guard case .connectivity(let code) = failure,
                      let baseDelay = recoveryBudget.takeDelay(
                    method: method,
                    connectivityCode: code
                ) else {
                    throw error
                }
                let jitter = UInt64.random(
                    in: 0...min(baseDelay / 5, 100_000_000)
                )
                let delay = baseDelay + jitter
                logger.info(
                    "Safe read retry scheduled | endpoint=\(endpoint, privacy: .public) method=\(method.rawValue, privacy: .public) transport_code=\(code.rawValue, privacy: .public) delay_ms=\(delay / 1_000_000, privacy: .public)"
                )
                try await Task.sleep(nanoseconds: delay)
            }
        }
    }

    private func shouldTreatAsAuthFailure(
        statusCode: Int,
        response: HTTPURLResponse,
        errorCode: String?,
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

        switch errorCode {
        case "authentication_required", "authentication_expired", "invalid_credentials",
             "invalid_token":
            return true
        default:
            return false
        }
    }

}
