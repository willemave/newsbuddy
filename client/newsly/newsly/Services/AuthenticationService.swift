//
//  AuthenticationService.swift
//  newsly
//
//  Created by Assistant on 10/25/25.
//

import Foundation
import AuthenticationServices
import CryptoKit
import os.log

private let authLogger = Logger(subsystem: "com.newsly", category: "AuthenticationService")

enum CredentialSessionEndResult: Equatable, Sendable {
    case ended
    case noLongerCurrent
    case failed
}

protocol AuthenticationServicing: AnyObject {
    @MainActor
    func signInWithApple() async throws -> AuthSession

    /// Ends the current session. Terminal refresh failures pass their event so
    /// a delayed failure cannot clear a newer account publication.
    @discardableResult
    func logout(matching event: CredentialTerminalEvent?) async -> CredentialSessionEndResult
    func getCurrentUser() async throws -> User

    #if DEBUG
    @MainActor
    func createDebugSession(
        userId: Int?,
        hasCompletedOnboarding: Bool?,
        hasCompletedNewUserTutorial: Bool?
    ) async throws -> AuthSession
    #endif
}

/// Authentication service handling Apple Sign In and token management
final class AuthenticationService: NSObject {
    static let shared = AuthenticationService()

    private override init() {
        super.init()
    }

    /// Sign in with Apple
    @MainActor
    func signInWithApple() async throws -> AuthSession {
        let nonce = randomNonceString()

        let appleIDProvider = ASAuthorizationAppleIDProvider()
        let request = appleIDProvider.createRequest()
        request.requestedScopes = [.fullName, .email]
        request.nonce = sha256(nonce)

        let authController = ASAuthorizationController(authorizationRequests: [request])

        let credentials: AppleSignInCredentials = try await withCheckedThrowingContinuation { continuation in
            let delegate = AppleSignInDelegate(continuation: continuation)
            authController.delegate = delegate
            authController.presentationContextProvider = delegate

            // Keep delegate alive
            objc_setAssociatedObject(authController, "delegate", delegate, .OBJC_ASSOCIATION_RETAIN)

            authController.performRequests()
        }
        try Task.checkCancellation()
        return try await sendAppleCredentialsToBackend(credentials)
    }

    /// Logout user. Credential deletion is serialized with refresh rotation so
    /// an exchange already in flight cannot publish a token pair after logout.
    @discardableResult
    func logout(
        matching event: CredentialTerminalEvent?
    ) async -> CredentialSessionEndResult {
        // Explicit user logout tears down presentation state immediately. A
        // conditional terminal event does so only after its credential identity
        // is proven current under the refresh lock.
        if event == nil {
            performLocalLogoutSideEffects()
        }
        let didClear: Bool
        do {
            didClear = try await CredentialSession.shared.clearCredentials(ifCurrent: event)
        } catch {
            authLogger.error("Credential clear failed during logout: \(error.localizedDescription)")
            return .failed
        }
        guard didClear else {
            return event == nil ? .failed : .noLongerCurrent
        }
        if event != nil {
            performLocalLogoutSideEffects()
        }
        return .ended
    }

    private func performLocalLogoutSideEffects() {
        BriefingSnapshotStore.invalidateAllSnapshots()
        KeychainManager.shared.deleteLegacyToken(named: "openaiApiKey")
        NotificationCenter.default.post(name: .authDidLogOut, object: nil)
    }

    /// Reauthenticate with Apple and request server-side revocation. The
    /// authentication controller ends the local session after this succeeds.
    @MainActor
    func deleteAccount() async throws {
        let provider = ASAuthorizationAppleIDProvider()
        let request = provider.createRequest()
        let controller = ASAuthorizationController(authorizationRequests: [request])
        let credentials = try await withCheckedThrowingContinuation { continuation in
            let delegate = AppleAccountDeletionDelegate(continuation: continuation)
            controller.delegate = delegate
            controller.presentationContextProvider = delegate
            objc_setAssociatedObject(controller, "deletionDelegate", delegate, .OBJC_ASSOCIATION_RETAIN)
            controller.performRequests()
        }

        do {
            let _: APIDeleteAccountResponse = try await APIClient.shared.request(
                APIEndpoints.authMe,
                method: .delete,
                body: JSONEncoder().encode(credentials),
                allowedStatusCodes: [202],
                authentication: .required
            )
        } catch {
            throw mapAuthenticationClientError(error)
        }
    }

    /// Get current user from backend
    func getCurrentUser() async throws -> User {
        let response: APIUserResponse = try await APIClient.shared.request(
            APIEndpoints.authMe,
            recoveryPolicy: .safeRead,
            authentication: .required,
            decoding: .iso8601
        )
        let user = User(api: response)
        try Task.checkCancellation()
        // Legacy loose tokens establish identity only after this server
        // validation succeeds.
        try await CredentialSession.shared.bindCurrentCredentials(to: user.id)
        try Task.checkCancellation()
        return user
    }

    /// Update authenticated user profile fields.
    func updateCurrentUserProfile(
        fullName: String? = nil,
        twitterUsername: String? = nil,
        councilPersonas: [CouncilPersona]? = nil,
        readingExperience: ReadingExperience? = nil
    ) async throws -> User {
        let body = APIUpdateUserProfileRequest(
            fullName: fullName,
            twitterUsername: twitterUsername,
            councilPersonas: councilPersonas?.map(\.apiInput),
            readingExperience: readingExperience
        )
        do {
            let response: APIUserResponse = try await APIClient.shared.request(
                APIEndpoints.authMe,
                method: .patch,
                body: JSONEncoder().encode(body),
                authentication: .required,
                decoding: .iso8601
            )
            return User(api: response)
        } catch {
            throw mapAuthenticationClientError(error)
        }
    }

    #if DEBUG
    /// Create or resume a debug session (debug servers only).
    @MainActor
    func createDebugSession(
        userId: Int? = nil,
        hasCompletedOnboarding: Bool? = nil,
        hasCompletedNewUserTutorial: Bool? = nil
    ) async throws -> AuthSession {
        let body = try JSONEncoder().encode(
            APIDebugUserSessionRequest(
                userId: userId,
                hasCompletedOnboarding: hasCompletedOnboarding,
                hasCompletedNewUserTutorial: hasCompletedNewUserTutorial,
                readingExperience: nil
            )
        )

        do {
            let tokenResponse: APITokenResponse = try await APIClient.shared.request(
                APIEndpoints.authDebugNewUser,
                method: .post,
                body: body,
                authentication: .none,
                decoding: .iso8601
            )
            try Task.checkCancellation()
            try await persistSessionTokens(tokenResponse)
            return AuthSession(
                user: User(api: tokenResponse.user),
                isNewUser: tokenResponse.isNewUser
            )
        } catch {
            throw mapAuthenticationClientError(error)
        }
    }
    #endif

    // MARK: - Private Helpers

    private func randomNonceString(length: Int = 32) -> String {
        precondition(length > 0)
        let charset: [Character] = Array("0123456789ABCDEFGHIJKLMNOPQRSTUVXYZabcdefghijklmnopqrstuvwxyz-._")
        var result = ""
        var remainingLength = length

        while remainingLength > 0 {
            let randoms: [UInt8] = (0..<16).map { _ in
                var random: UInt8 = 0
                let errorCode = SecRandomCopyBytes(kSecRandomDefault, 1, &random)
                if errorCode != errSecSuccess {
                    fatalError("Unable to generate nonce. SecRandomCopyBytes failed with OSStatus \(errorCode)")
                }
                return random
            }

            randoms.forEach { random in
                if remainingLength == 0 {
                    return
                }

                if random < charset.count {
                    result.append(charset[Int(random)])
                    remainingLength -= 1
                }
            }
        }

        return result
    }

    private func sha256(_ input: String) -> String {
        let inputData = Data(input.utf8)
        let hashedData = SHA256.hash(data: inputData)
        let hashString = hashedData.compactMap {
            String(format: "%02x", $0)
        }.joined()

        return hashString
    }

    @MainActor
    private func sendAppleCredentialsToBackend(
        _ credentials: AppleSignInCredentials
    ) async throws -> AuthSession {
        let request = APIAppleSignInRequest(
            idToken: credentials.identityToken,
            email: nonEmptyAuthField(credentials.email),
            fullName: nonEmptyAuthField(credentials.fullName)
        )
        let tokenResponse: APITokenResponse = try await APIClient.shared.request(
            "/auth/apple",
            method: .post,
            body: JSONEncoder().encode(request),
            authentication: .none,
            decoding: .iso8601
        )
        try Task.checkCancellation()
        try await persistSessionTokens(tokenResponse)
        return AuthSession(
            user: User(api: tokenResponse.user),
            isNewUser: tokenResponse.isNewUser
        )
    }
}

@MainActor
private final class AppleAccountDeletionDelegate: NSObject, ASAuthorizationControllerDelegate,
    ASAuthorizationControllerPresentationContextProviding
{
    let continuation: CheckedContinuation<APIDeleteAccountRequest, Error>

    init(continuation: CheckedContinuation<APIDeleteAccountRequest, Error>) {
        self.continuation = continuation
    }

    func authorizationController(
        controller: ASAuthorizationController,
        didCompleteWithAuthorization authorization: ASAuthorization
    ) {
        guard let credential = authorization.credential as? ASAuthorizationAppleIDCredential,
              let tokenData = credential.identityToken,
              let codeData = credential.authorizationCode,
              let idToken = String(data: tokenData, encoding: .utf8),
              let authorizationCode = String(data: codeData, encoding: .utf8) else {
            continuation.resume(throwing: AuthError.appleSignInFailed)
            return
        }
        continuation.resume(
            returning: APIDeleteAccountRequest(
                idToken: idToken,
                authorizationCode: authorizationCode
            )
        )
    }

    func authorizationController(
        controller: ASAuthorizationController,
        didCompleteWithError error: Error
    ) {
        continuation.resume(throwing: error)
    }

    func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        applePresentationAnchor()
    }
}

extension AuthenticationService: AuthenticationServicing {}

// MARK: - Apple Sign In Delegate

private struct AppleSignInCredentials {
    let identityToken: String
    let email: String?
    let fullName: String?
}

@MainActor
private class AppleSignInDelegate: NSObject, ASAuthorizationControllerDelegate, ASAuthorizationControllerPresentationContextProviding {
    let continuation: CheckedContinuation<AppleSignInCredentials, Error>

    init(continuation: CheckedContinuation<AppleSignInCredentials, Error>) {
        self.continuation = continuation
    }

    func authorizationController(controller: ASAuthorizationController, didCompleteWithAuthorization authorization: ASAuthorization) {
        guard let appleIDCredential = authorization.credential as? ASAuthorizationAppleIDCredential else {
            continuation.resume(throwing: AuthError.appleSignInFailed)
            return
        }

        guard let identityTokenData = appleIDCredential.identityToken,
              let identityToken = String(data: identityTokenData, encoding: .utf8) else {
            continuation.resume(throwing: AuthError.appleSignInFailed)
            return
        }

        let fullName = appleIDCredential.fullName.flatMap { components -> String? in
            let parts = [components.givenName, components.familyName].compactMap { $0 }
            return parts.isEmpty ? nil : parts.joined(separator: " ")
        }
        continuation.resume(
            returning: AppleSignInCredentials(
                identityToken: identityToken,
                email: appleIDCredential.email,
                fullName: fullName
            )
        )
    }

    func authorizationController(controller: ASAuthorizationController, didCompleteWithError error: Error) {
        continuation.resume(throwing: error)
    }

    func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        applePresentationAnchor()
    }

}

@MainActor
private func applePresentationAnchor() -> ASPresentationAnchor {
    let windowScenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
    guard let windowScene = windowScenes.first(where: { $0.activationState == .foregroundActive })
        ?? windowScenes.first
    else {
        preconditionFailure("Apple Sign In requires a connected window scene")
    }
    return windowScene.windows.first(where: { $0.isKeyWindow })
        ?? windowScene.windows.first
        ?? ASPresentationAnchor(windowScene: windowScene)
}

private func persistSessionTokens(_ tokenResponse: APITokenResponse) async throws {
    try await CredentialSession.shared.publishAuthenticated(
        tokens: CredentialTokens(
            accessToken: tokenResponse.accessToken,
            refreshToken: tokenResponse.refreshToken
        ),
        userID: tokenResponse.user.id
    )
    KeychainManager.shared.deleteLegacyToken(named: "openaiApiKey")
}

private func nonEmptyAuthField(_ value: String?) -> String? {
    guard let value, !value.isEmpty else { return nil }
    return value
}

private func mapAuthenticationClientError(_ error: Error) -> Error {
    if let authError = error as? AuthError { return authError }
    switch ClientFailure.classify(error) {
    case .cancelled:
        return CancellationError()
    case .connectivity(let code):
        return AuthError.networkError(URLError(code))
    case .authenticationRequired, .authenticationExpired:
        return AuthError.notAuthenticated
    case .server(let statusCode, let error):
        return AuthError.serverError(statusCode: statusCode, message: error.message)
    case .http(let statusCode, let detail):
        return AuthError.serverError(statusCode: statusCode, message: detail)
    case .invalidRequest, .invalidResponse, .decoding, .unexpected:
        return AuthError.refreshFailed
    }
}

extension Notification.Name {
    static let authDidLogOut = Notification.Name("authDidLogOut")
}
