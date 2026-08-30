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

/// The extension's authenticated submission surface. It shares the mechanical
/// transport, refresh exchange, credential reconciliation, and refresh
/// single-flight implementation with the app.
final class ShareExtensionTransport {
    static let shared = ShareExtensionTransport()

    private let client: APIClient

    init(
        session: URLSession = ShareExtensionTransport.makeSession(),
        tokenStore: any AuthTokenStore = KeychainManager.shared,
        processLock: AuthRefreshProcessLock = .shared,
        attemptStore: (any RefreshAttemptStoring)? = nil,
        refreshRetryDelaysNanoseconds: [UInt64] = [250_000_000],
        credentialSession: (any CredentialSessionProviding)? = nil,
        baseURLProvider: @escaping () -> URL? = {
            ServerConfigurationDefaults.baseURL(in: SharedContainer.userDefaults)
        }
    ) {
        let transport = HTTPTransport(session: session)
        let credentialSession = credentialSession ?? CredentialSession(
            storage: CredentialStorageFactory.make(tokenStore: tokenStore),
            exchange: RefreshTokenExchange(
                transport: transport,
                baseURLProvider: baseURLProvider,
                retryDelaysNanoseconds: refreshRetryDelaysNanoseconds
            ),
            processLock: processLock,
            attemptStore: attemptStore ?? Self.makeAttemptStore(tokenStore: tokenStore),
            cooldownSeconds: 0
        )
        client = APIClient(
            transport: transport,
            baseURLProvider: baseURLProvider,
            credentialSession: credentialSession
        )
    }

    func requestVoid(
        _ endpoint: String,
        method: HTTPMethod = .post,
        body: Data? = nil
    ) async throws {
        do {
            try await client.requestVoid(
                endpoint,
                method: method,
                body: body,
                authentication: .required
            )
        } catch {
            throw mapClientFailure(error)
        }
    }

    private func mapClientFailure(_ error: Error) -> ShareExtensionTransportError {
        switch ClientFailure.classify(error) {
        case .authenticationRequired, .authenticationExpired:
            return .notAuthenticated
        case .invalidRequest:
            return .invalidURL
        case .invalidResponse, .decoding:
            return .invalidResponse
        case .http(let statusCode, let detail):
            return .server(statusCode: statusCode, detail: detail)
        case .cancelled, .connectivity, .unexpected:
            return .network(error)
        }
    }

    private static func makeSession() -> URLSession {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 60
        return URLSession(configuration: configuration)
    }

    private static func makeAttemptStore(
        tokenStore: any AuthTokenStore
    ) -> any RefreshAttemptStoring {
        guard let persistence = tokenStore as? any RefreshAttemptPersisting else {
            return UnavailableRefreshAttemptStore()
        }
        return KeychainRefreshAttemptStore(persistence: persistence)
    }
}

private final class UnavailableRefreshAttemptStore: RefreshAttemptStoring {
    func attemptID(for _: String) throws -> String {
        throw RefreshAttemptStoreError.storageUnavailable
    }

    func clearAttempt(for _: String) {}
}

private func nonEmptyDetail(_ value: String?) -> String? {
    guard let value else { return nil }
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.isEmpty ? nil : trimmed
}
