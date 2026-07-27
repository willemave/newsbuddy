//
//  ShareExtensionTransport.swift
//  newsly
//

import Foundation

enum ShareExtensionTransportError: LocalizedError {
    case invalidURL
    case notAuthenticated
    case invalidResponse
    case network(Error)
    case server(statusCode: Int, detail: String?)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            "Invalid URL"
        case .notAuthenticated:
            "Not authenticated"
        case .invalidResponse:
            "Invalid server response"
        case .network(let error):
            error.localizedDescription
        case .server(let statusCode, let detail):
            nonEmptyDetail(detail) ?? "Request failed with status \(statusCode)"
        }
    }
}

/// The extension's intentionally small authenticated transport surface.
///
/// Keeping this separate prevents the extension target from compiling the main app's API client,
/// settings object, token-refresh coordinator, and generated contract graph for one void request.
final class ShareExtensionTransport {
    static let shared = ShareExtensionTransport()

    private let session: URLSession
    private let tokenStore: any AuthTokenStore
    private let baseURLProvider: () -> URL?

    init(
        session: URLSession = ShareExtensionTransport.makeSession(),
        tokenStore: any AuthTokenStore = KeychainManager.shared,
        baseURLProvider: @escaping () -> URL? = {
            ServerConfigurationDefaults.baseURL(in: SharedContainer.userDefaults)
        }
    ) {
        self.session = session
        self.tokenStore = tokenStore
        self.baseURLProvider = baseURLProvider
    }

    func requestVoid(
        _ endpoint: String,
        method: String = "POST",
        body: Data? = nil
    ) async throws {
        let accessToken = try await currentOrRefreshedAccessToken()
        try await execute(
            endpoint: endpoint,
            method: method,
            body: body,
            accessToken: accessToken,
            allowsRefresh: true
        )
    }

    private func execute(
        endpoint: String,
        method: String,
        body: Data?,
        accessToken: String,
        allowsRefresh: Bool
    ) async throws {
        let request = try makeRequest(
            endpoint: endpoint,
            method: method,
            body: body,
            accessToken: accessToken
        )
        let (data, response) = try await send(request)

        if isAuthenticationFailure(response: response, data: data) {
            guard allowsRefresh else {
                throw ShareExtensionTransportError.notAuthenticated
            }
            let refreshedToken = try await refreshAccessToken()
            try await execute(
                endpoint: endpoint,
                method: method,
                body: body,
                accessToken: refreshedToken,
                allowsRefresh: false
            )
            return
        }

        guard (200...299).contains(response.statusCode) else {
            throw ShareExtensionTransportError.server(
                statusCode: response.statusCode,
                detail: errorDetail(from: data)
            )
        }
    }

    private func currentOrRefreshedAccessToken() async throws -> String {
        if let token = nonEmptyToken(tokenStore.getToken(key: .accessToken)) {
            return token
        }
        return try await refreshAccessToken()
    }

    private func refreshAccessToken() async throws -> String {
        guard let refreshToken = nonEmptyToken(tokenStore.getToken(key: .refreshToken)) else {
            throw ShareExtensionTransportError.notAuthenticated
        }
        let payload = ShareExtensionRefreshRequest(refreshToken: refreshToken)
        let request = try makeRequest(
            endpoint: "/auth/refresh",
            method: "POST",
            body: try JSONEncoder().encode(payload),
            accessToken: nil
        )
        let (data, response) = try await send(request)

        switch response.statusCode {
        case 200...299:
            do {
                let tokens = try JSONDecoder().decode(ShareExtensionRefreshResponse.self, from: data)
                tokenStore.saveToken(tokens.accessToken, key: .accessToken)
                tokenStore.saveToken(tokens.refreshToken, key: .refreshToken)
                return tokens.accessToken
            } catch {
                throw ShareExtensionTransportError.invalidResponse
            }
        case 401, 403:
            tokenStore.deleteToken(key: .accessToken)
            tokenStore.deleteToken(key: .refreshToken)
            throw ShareExtensionTransportError.notAuthenticated
        default:
            throw ShareExtensionTransportError.server(
                statusCode: response.statusCode,
                detail: errorDetail(from: data)
            )
        }
    }

    private func makeRequest(
        endpoint: String,
        method: String,
        body: Data?,
        accessToken: String?
    ) throws -> URLRequest {
        guard
            let baseURL = baseURLProvider(),
            let url = URL(string: endpoint, relativeTo: baseURL)?.absoluteURL
        else {
            throw ShareExtensionTransportError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let accessToken {
            request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = body
        return request
    }

    private func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        do {
            let (data, response) = try await session.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse else {
                throw ShareExtensionTransportError.invalidResponse
            }
            return (data, httpResponse)
        } catch let error as ShareExtensionTransportError {
            throw error
        } catch {
            throw ShareExtensionTransportError.network(error)
        }
    }

    private func isAuthenticationFailure(response: HTTPURLResponse, data: Data) -> Bool {
        if response.statusCode == 401 {
            return true
        }
        guard response.statusCode == 403 else {
            return false
        }
        if response.value(forHTTPHeaderField: "WWW-Authenticate")?
            .localizedCaseInsensitiveContains("bearer") == true {
            return true
        }
        guard let detail = errorDetail(from: data)?.lowercased() else {
            return false
        }
        return [
            "not authenticated",
            "could not validate credentials",
            "invalid token",
            "token expired",
            "expired token",
            "missing token",
            "unauthorized",
        ].contains { detail.contains($0) }
    }

    private func errorDetail(from data: Data) -> String? {
        guard !data.isEmpty else { return nil }
        if let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            for key in ["detail", "message", "error", "error_message"] {
                if let value = nonEmptyDetail(payload[key] as? String) {
                    return value
                }
            }
        }
        return nonEmptyDetail(String(data: data, encoding: .utf8))
    }

    private static func makeSession() -> URLSession {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 60
        return URLSession(configuration: configuration)
    }
}

private struct ShareExtensionRefreshRequest: Encodable {
    let refreshToken: String

    enum CodingKeys: String, CodingKey {
        case refreshToken = "refresh_token"
    }
}

private struct ShareExtensionRefreshResponse: Decodable {
    let accessToken: String
    let refreshToken: String

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
    }
}

private func nonEmptyToken(_ value: String?) -> String? {
    nonEmptyDetail(value)
}

private func nonEmptyDetail(_ value: String?) -> String? {
    guard let value else { return nil }
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.isEmpty ? nil : trimmed
}
