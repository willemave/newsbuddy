//
//  XIntegrationService.swift
//  newsly
//

import AuthenticationServices
import Foundation
import UIKit

protocol XIntegrationAPIClientProtocol {
    func fetchConnection() async throws -> XConnectionResponse
    func startOAuth() async throws -> XOAuthStartResponse
    func exchangeOAuth(code: String, state: String) async throws -> XConnectionResponse
    func disconnect() async throws
}

struct XOAuthStartRequest: Codable {}

struct XOAuthStartResponse: Codable {
    let authorizeURL: String
    let state: String
    let scopes: [String]

    enum CodingKeys: String, CodingKey {
        case authorizeURL = "authorize_url"
        case state
        case scopes
    }
}

struct XOAuthExchangeRequest: Codable {
    let code: String
    let state: String
}

struct XConnectionResponse: Codable {
    let provider: String
    let connected: Bool
    let isActive: Bool
    let providerUserID: String?
    let providerUsername: String?
    let scopes: [String]
    let lastSyncedAt: String?
    let lastStatus: String?
    let lastError: String?

    enum CodingKeys: String, CodingKey {
        case provider
        case connected
        case isActive = "is_active"
        case providerUserID = "provider_user_id"
        case providerUsername = "provider_username"
        case scopes
        case lastSyncedAt = "last_synced_at"
        case lastStatus = "last_status"
        case lastError = "last_error"
    }
}

extension XConnectionResponse {
    var needsAttention: Bool {
        if connected {
            return false
        }
        if lastError?.isEmpty == false {
            return true
        }
        if let lastStatus, !lastStatus.isEmpty, lastStatus != "never_synced" {
            return true
        }
        return !isActive && providerUsername != nil
    }

    var settingsSubtitle: String? {
        if needsAttention {
            return issueSummary
        }
        if let providerUsername, !providerUsername.isEmpty {
            return "@\(providerUsername)"
        }
        if let lastStatus, !lastStatus.isEmpty, lastStatus != "never_synced" {
            return "Status: \(formattedStatus(lastStatus))"
        }
        return nil
    }

    var issueSummary: String {
        if isReauthRequired {
            return "Reconnect required"
        }
        if let lastStatus, !lastStatus.isEmpty, lastStatus != "never_synced" {
            return formattedStatus(lastStatus)
        }
        return "Connection problem"
    }

    var issueTitle: String {
        if isReauthRequired {
            return "Reconnect X to resume bookmark sync"
        }
        return "X bookmark sync needs attention"
    }

    var issueMessage: String {
        if isReauthRequired {
            return "Your saved X session expired or was rejected. Reconnect your account to restore bookmark sync."
        }
        if !isActive {
            return "This X connection is inactive, so bookmarked posts cannot sync into your long-form feed."
        }
        return "Newsbuddy could not complete X bookmark sync. Reconnect your account if this keeps happening."
    }

    var issueDetails: String? {
        guard let lastError, !lastError.isEmpty else { return nil }
        if isReauthRequired && lastError.localizedCaseInsensitiveContains("reauthentication") {
            return nil
        }
        return lastError
    }

    var connectActionTitle: String {
        needsAttention ? "Reconnect X" : "Connect X"
    }

    var connectActionSubtitle: String {
        if needsAttention {
            return "Reconnect bookmark sync from your X account"
        }
        return "Authorize bookmark sync from your X account"
    }

    private var isReauthRequired: Bool {
        if lastStatus == "reauth_required" {
            return true
        }
        guard let lastError else { return false }
        let lowered = lastError.lowercased()
        return lowered.contains("invalid_request")
            || lowered.contains("invalid_grant")
            || lowered.contains("reauth")
    }

    private func formattedStatus(_ value: String) -> String {
        value
            .split(separator: "_")
            .map { segment in
                let lowered = segment.lowercased()
                return lowered.prefix(1).uppercased() + lowered.dropFirst()
            }
            .joined(separator: " ")
    }
}

enum XIntegrationError: LocalizedError {
    case invalidAuthorizeURL
    case missingCallbackCode
    case missingCallbackState
    case oauthFailed(String)
    case oauthCancelled
    case oauthSessionStartFailed
    case callbackParsingFailed

    var errorDescription: String? {
        switch self {
        case .invalidAuthorizeURL:
            return "Invalid OAuth authorize URL"
        case .missingCallbackCode:
            return "OAuth callback missing code"
        case .missingCallbackState:
            return "OAuth callback missing state"
        case .oauthFailed(let message):
            return "OAuth failed: \(message)"
        case .oauthCancelled:
            return "OAuth was cancelled"
        case .oauthSessionStartFailed:
            return "Unable to start OAuth session"
        case .callbackParsingFailed:
            return "Unable to parse OAuth callback URL"
        }
    }
}

final class XIntegrationService {
    static let shared = XIntegrationService()

    typealias OAuthSessionHandler = @MainActor (URL) async throws -> URL

    private let client: XIntegrationAPIClientProtocol
    private let callbackScheme = "newsly"
    private let presentationContextProvider = OAuthPresentationContextProvider()
    private let oauthSessionHandler: OAuthSessionHandler?
    private var authSession: ASWebAuthenticationSession?

    init(
        client: XIntegrationAPIClientProtocol = LiveXIntegrationAPIClient(),
        oauthSessionHandler: OAuthSessionHandler? = nil
    ) {
        self.client = client
        self.oauthSessionHandler = oauthSessionHandler
    }

    func fetchConnection() async throws -> XConnectionResponse {
        try await client.fetchConnection()
    }

    func startOAuth() async throws -> XOAuthStartResponse {
        try await client.startOAuth()
    }

    func exchangeOAuth(code: String, state: String) async throws -> XConnectionResponse {
        try await client.exchangeOAuth(code: code, state: state)
    }

    func disconnect() async throws {
        try await client.disconnect()
    }

    @MainActor
    func connectViaOAuth() async throws -> XConnectionResponse {
        let start = try await startOAuth()
        guard let authorizeURL = URL(string: start.authorizeURL) else {
            throw XIntegrationError.invalidAuthorizeURL
        }

        let callbackURL: URL
        if let oauthSessionHandler {
            callbackURL = try await oauthSessionHandler(authorizeURL)
        } else {
            callbackURL = try await runOAuthSession(authorizeURL: authorizeURL)
        }
        guard let components = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false),
              let queryItems = components.queryItems else {
            throw XIntegrationError.callbackParsingFailed
        }

        if let oauthError = queryItems.first(where: { $0.name == "error" })?.value {
            let description = queryItems.first(where: { $0.name == "error_description" })?.value
            throw XIntegrationError.oauthFailed(description ?? oauthError)
        }

        guard let code = queryItems.first(where: { $0.name == "code" })?.value, !code.isEmpty else {
            throw XIntegrationError.missingCallbackCode
        }

        guard let state = queryItems.first(where: { $0.name == "state" })?.value, !state.isEmpty else {
            throw XIntegrationError.missingCallbackState
        }

        return try await exchangeOAuth(code: code, state: state)
    }

    @MainActor
    private func runOAuthSession(authorizeURL: URL) async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: authorizeURL,
                callbackURLScheme: callbackScheme
            ) { callbackURL, error in
                if let error = error as? ASWebAuthenticationSessionError {
                    if error.code == .canceledLogin {
                        continuation.resume(throwing: XIntegrationError.oauthCancelled)
                    } else {
                        continuation.resume(throwing: error)
                    }
                    return
                }
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                guard let callbackURL else {
                    continuation.resume(throwing: XIntegrationError.callbackParsingFailed)
                    return
                }
                continuation.resume(returning: callbackURL)
            }

            session.prefersEphemeralWebBrowserSession = false
            session.presentationContextProvider = presentationContextProvider
            authSession = session

            if !session.start() {
                continuation.resume(throwing: XIntegrationError.oauthSessionStartFailed)
            }
        }
    }

}

private struct LiveXIntegrationAPIClient: XIntegrationAPIClientProtocol {
    private let client: APIClient

    init(client: APIClient = .shared) {
        self.client = client
    }

    func fetchConnection() async throws -> XConnectionResponse {
        try await client.request(APIEndpoints.xIntegrationConnection)
    }

    func startOAuth() async throws -> XOAuthStartResponse {
        let body = try JSONEncoder().encode(XOAuthStartRequest())
        return try await client.request(
            APIEndpoints.xIntegrationOAuthStart,
            method: .post,
            body: body
        )
    }

    func exchangeOAuth(code: String, state: String) async throws -> XConnectionResponse {
        let body = try JSONEncoder().encode(XOAuthExchangeRequest(code: code, state: state))
        return try await client.request(
            APIEndpoints.xIntegrationOAuthExchange,
            method: .post,
            body: body
        )
    }

    func disconnect() async throws {
        try await client.requestVoid(APIEndpoints.xIntegrationConnection, method: .delete)
    }
}

private final class OAuthPresentationContextProvider: NSObject, ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        let windowScenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let windowScene = windowScenes.first(where: { $0.activationState == .foregroundActive })
            ?? windowScenes.first
        else {
            preconditionFailure("X OAuth requires a connected window scene")
        }
        return windowScene.windows.first(where: { $0.isKeyWindow })
            ?? windowScene.windows.first
            ?? ASPresentationAnchor(windowScene: windowScene)
    }
}
