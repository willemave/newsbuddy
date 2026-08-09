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

enum ShareSubmissionFailure: Equatable {
    case invalidURL
    case authenticationRequired
    case recoverable
}

enum ShareSubmissionRecoveryAction: Equatable {
    case retry
    case openApp
}

/// Pure presentation state shared by the extension controller and native tests.
/// Network transport remains separate; this model only owns submission and recovery transitions.
struct ShareSubmissionPresentationState: Equatable {
    enum Phase: Equatable {
        case ready
        case submitting
        case failed(ShareSubmissionFailure)
        case completed
        case manualOpenFallback
    }

    private(set) var phase: Phase = .ready

    var isSubmitting: Bool {
        phase == .submitting
    }

    var canBeginSubmission: Bool {
        switch phase {
        case .ready, .failed(.invalidURL), .failed(.recoverable):
            true
        case .submitting, .failed(.authenticationRequired), .completed, .manualOpenFallback:
            false
        }
    }

    var recoveryAction: ShareSubmissionRecoveryAction? {
        switch phase {
        case .failed(.invalidURL), .ready, .submitting, .completed, .manualOpenFallback:
            nil
        case .failed(.authenticationRequired):
            .openApp
        case .failed(.recoverable):
            .retry
        }
    }

    mutating func begin(hasValidURL: Bool) -> Bool {
        guard canBeginSubmission else { return false }
        guard hasValidURL else {
            phase = .failed(.invalidURL)
            return false
        }
        phase = .submitting
        return true
    }

    mutating func fail(_ failure: ShareSubmissionFailure) {
        phase = .failed(failure)
    }

    mutating func succeed() {
        phase = .completed
    }

    mutating func finishOpeningApp(opened: Bool) {
        guard phase == .failed(.authenticationRequired) else { return }
        phase = opened ? .completed : .manualOpenFallback
    }

    mutating func finishManualFallback() {
        guard phase == .manualOpenFallback else { return }
        phase = .completed
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
    private let processLock: AuthRefreshProcessLock
    private let refreshCoordinator = ShareExtensionRefreshCoordinator()

    init(
        session: URLSession = ShareExtensionTransport.makeSession(),
        tokenStore: any AuthTokenStore = KeychainManager.shared,
        processLock: AuthRefreshProcessLock = .shared,
        baseURLProvider: @escaping () -> URL? = {
            ServerConfigurationDefaults.baseURL(in: SharedContainer.userDefaults)
        }
    ) {
        self.session = session
        self.tokenStore = tokenStore
        self.processLock = processLock
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
        let task = await refreshCoordinator.task { [weak self] in
            guard let self else { throw ShareExtensionTransportError.notAuthenticated }
            return try await self.processLock.withLock {
                // The main app may have rotated while this extension waited.
                try await self.performRefreshAccessToken()
            }
        }
        return try await task.value
    }

    private func performRefreshAccessToken(rotatedRetryCount: Int = 1) async throws -> String {
        guard let refreshToken = nonEmptyToken(tokenStore.getToken(key: .refreshToken)) else {
            throw ShareExtensionTransportError.notAuthenticated
        }
        let attemptedAccessToken = nonEmptyToken(tokenStore.getToken(key: .accessToken))
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
                // Make rotation visible before the access token so a concurrent app
                // process never clears the newly issued one-time credential.
                tokenStore.saveToken(tokens.refreshToken, key: .refreshToken)
                tokenStore.saveToken(tokens.accessToken, key: .accessToken)
                return tokens.accessToken
            } catch {
                throw ShareExtensionTransportError.invalidResponse
            }
        case 401, 403:
            let currentRefreshToken = nonEmptyToken(tokenStore.getToken(key: .refreshToken))
            if currentRefreshToken != refreshToken {
                if let currentAccessToken = nonEmptyToken(tokenStore.getToken(key: .accessToken)),
                   currentAccessToken != attemptedAccessToken {
                    return currentAccessToken
                }
                if rotatedRetryCount > 0, currentRefreshToken != nil {
                    return try await performRefreshAccessToken(
                        rotatedRetryCount: rotatedRetryCount - 1
                    )
                }
                throw ShareExtensionTransportError.notAuthenticated
            }

            // Leave cleanup to the main app's authenticated lifecycle. A mixed-version
            // app/extension pair must never be able to delete newly rotated credentials.
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

private actor ShareExtensionRefreshCoordinator {
    private struct ActiveRefresh {
        let id: UUID
        let task: Task<String, Error>
    }

    private var activeRefresh: ActiveRefresh?

    func task(
        operation: @escaping @Sendable () async throws -> String
    ) -> Task<String, Error> {
        if let activeRefresh {
            return activeRefresh.task
        }

        let refreshID = UUID()
        let task = Task { try await operation() }
        activeRefresh = ActiveRefresh(id: refreshID, task: task)
        Task { [weak self] in
            _ = try? await task.value
            await self?.finish(refreshID: refreshID)
        }
        return task
    }

    private func finish(refreshID: UUID) {
        guard activeRefresh?.id == refreshID else { return }
        activeRefresh = nil
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
