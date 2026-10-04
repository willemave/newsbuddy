//
//  ShareExtensionTransport.swift
//  newsly
//

import Foundation

enum ShareSubmissionFailure: Equatable {
    case invalidURL
    case authenticationRequired
    case recoverable
}

enum ShareSubmissionRecoveryAction: Equatable {
    case retry
    case openApp
}

struct ShareActionAcceptance: Decodable, Equatable {
    let taskId: Int
    let mode: APILlmTaskMode

    private enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case mode
    }
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
            storage: KeychainCredentialStorage(keychain: tokenStore),
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
        try await client.requestVoid(
            endpoint,
            method: method,
            body: body,
            authentication: .required
        )
    }

    func request<Response: Decodable>(
        _ endpoint: String,
        method: HTTPMethod = .post,
        body: Data? = nil
    ) async throws -> Response {
        try await client.request(
            endpoint,
            method: method,
            body: body,
            authentication: .required
        )
    }

    /// A successful POST has already committed the share action. Decode only
    /// the fields the extension uses, and treat an unfamiliar success body as
    /// accepted so presentation-only schema drift cannot invite a duplicate retry.
    func submitShareAction(body: Data) async throws -> ShareActionAcceptance? {
        let (data, _) = try await client.requestHTTP(
            "/api/share-actions",
            method: .post,
            body: body,
            authentication: .required
        )
        return try? JSONDecoder().decode(ShareActionAcceptance.self, from: data)
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
