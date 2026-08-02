//
//  AuthenticationViewModel.swift
//  newsly
//
//  Created by Assistant on 10/25/25.
//

import Foundation
import AuthenticationServices
import Observation
import SwiftUI
import os.log

private let authViewModelLogger = Logger(subsystem: "com.newsly", category: "AuthenticationViewModel")

/// Authentication state
enum AuthState: Equatable {
    case loading
    case unauthenticated
    case authenticated(User)
}

/// View model managing authentication state
@MainActor
@Observable
final class AuthenticationViewModel {
    var authState: AuthState = .loading
    var errorMessage: String?

    @ObservationIgnored
    private let authService: any AuthenticationServicing
    @ObservationIgnored
    private let tokenStore: any AuthTokenStore
    @ObservationIgnored
    private var lastKnownUser: User?
    #if DEBUG
    @ObservationIgnored
    private var hasAttemptedE2EAutoLogin = false
    #endif
    @ObservationIgnored
    private var authenticationRequiredObserver: NSObjectProtocol?

    init(
        authService: any AuthenticationServicing,
        tokenStore: any AuthTokenStore
    ) {
        self.authService = authService
        self.tokenStore = tokenStore

        checkAuthStatus()

        // This is the single direct observer for APIClient's auth-failure signal.
        // Other services should react to the logout notification emitted from here.
        authenticationRequiredObserver = NotificationCenter.default.addObserver(
            forName: .authenticationRequired,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            let endpoint = notification.userInfo?["endpoint"] as? String ?? "unknown"
            let reason = notification.userInfo?["reason"] as? String ?? "unknown"
            let status = notification.userInfo?["statusCode"] as? Int
            let detail = notification.userInfo?["detail"] as? String ?? "n/a"
            let statusText = status.map(String.init) ?? "n/a"

            authViewModelLogger.error(
                "[AuthState] Received authenticationRequired | endpoint=\(endpoint, privacy: .public) reason=\(reason, privacy: .public) status=\(statusText, privacy: .public) detail=\(detail, privacy: .public)"
            )

            Task { @MainActor in
                self?.logout()
            }
        }
    }

    deinit {
        if let authenticationRequiredObserver {
            NotificationCenter.default.removeObserver(authenticationRequiredObserver)
        }
    }

    /// Check if user is already authenticated on app launch
    func checkAuthStatus() {
        authState = .loading
        errorMessage = nil

        let hasRefreshToken = tokenStore.getToken(key: .refreshToken) != nil
        let hasAccessToken = tokenStore.getToken(key: .accessToken) != nil

        // No tokens at all -> user must sign in
        guard hasRefreshToken || hasAccessToken else {
            #if DEBUG
            if E2ETestLaunch.shouldAutoLogin && !hasAttemptedE2EAutoLogin {
                hasAttemptedE2EAutoLogin = true
                performE2EAutoLogin()
                return
            }
            #endif
            authState = .unauthenticated
            return
        }

        Task {
            do {
                let user = try await authService.getCurrentUser()
                errorMessage = nil
                lastKnownUser = user
                authState = .authenticated(user)
            } catch let authError as AuthError {
                await handleAuthFailure(authError, hasRefreshToken: hasRefreshToken)
            } catch {
                authState = .unauthenticated
            }
        }
    }

    /// Sign in with Apple
    func signInWithApple() {
        authState = .loading
        errorMessage = nil

        Task {
            do {
                let session = try await authService.signInWithApple()
                errorMessage = nil
                lastKnownUser = session.user
                authState = .authenticated(session.user)
            } catch {
                presentAuthError(error)
                authState = .unauthenticated
            }
        }
    }

    /// Logout current user
    func logout() {
        authService.logout()
        lastKnownUser = nil
        errorMessage = nil
        authState = .unauthenticated
    }

    func updateUser(_ user: User) {
        lastKnownUser = user
        authState = .authenticated(user)
    }

    #if DEBUG
    func startDebugSession(userID: Int) {
        authService.logout()
        lastKnownUser = nil
        errorMessage = nil
        authState = .loading

        Task {
            do {
                let session = try await authService.createDebugSession(
                    userId: userID,
                    hasCompletedOnboarding: nil,
                    hasCompletedNewUserTutorial: nil
                )
                lastKnownUser = session.user
                authState = .authenticated(session.user)
            } catch {
                presentAuthError(error)
                authState = .unauthenticated
            }
        }
    }
    #endif

    // MARK: - Private

    private func handleAuthFailure(_ error: AuthError, hasRefreshToken: Bool) async {
        switch error {
        case .notAuthenticated:
            guard hasRefreshToken else {
                authService.logout()
                authState = .unauthenticated
                return
            }
            await refreshAndLoadUser()
        case .refreshTokenExpired, .noRefreshToken:
            authService.logout()
            authState = .unauthenticated
        case .networkError(let underlying):
            errorMessage = AuthError.networkError(underlying).userFacingMessage
            // Keep tokens; allow retry without forcing logout
            if let user = lastKnownUser {
                authState = .authenticated(user)
            } else {
                authState = .unauthenticated
            }
        case .serverError(let statusCode, let message):
            errorMessage = AuthError.serverError(statusCode: statusCode, message: message).userFacingMessage
            if let user = lastKnownUser {
                authState = .authenticated(user)
            } else {
                authState = .unauthenticated
            }
        default:
            authService.logout()
            authState = .unauthenticated
        }
    }

    private func refreshAndLoadUser() async {
        do {
            _ = try await authService.refreshAccessToken()
            let user = try await authService.getCurrentUser()
            errorMessage = nil
            lastKnownUser = user
            authState = .authenticated(user)
            print("✅ User authenticated successfully after refresh")
        } catch let authError as AuthError {
            switch authError {
            case .refreshTokenExpired, .noRefreshToken:
                authService.logout()
                authState = .unauthenticated
            case .networkError(let underlying):
                errorMessage = AuthError.networkError(underlying).userFacingMessage
                if let user = lastKnownUser {
                    authState = .authenticated(user)
                } else {
                    authState = .unauthenticated
                }
            case .serverError(let statusCode, let message):
                errorMessage = AuthError.serverError(statusCode: statusCode, message: message).userFacingMessage
                if let user = lastKnownUser {
                    authState = .authenticated(user)
                } else {
                    authState = .unauthenticated
                }
            default:
                authService.logout()
                authState = .unauthenticated
            }
        } catch {
            authService.logout()
            authState = .unauthenticated
        }
    }

    #if DEBUG
    private func performE2EAutoLogin() {
        Task {
            do {
                let session = try await authService.createDebugSession(
                    userId: E2ETestLaunch.userID,
                    hasCompletedOnboarding: E2ETestLaunch.completeOnboarding,
                    hasCompletedNewUserTutorial: E2ETestLaunch.completeTutorial
                )
                errorMessage = nil
                lastKnownUser = session.user
                authState = .authenticated(session.user)
            } catch {
                presentAuthError(error)
                authState = .unauthenticated
            }
        }
    }
    #endif

    private func presentAuthError(_ error: Error) {
        if let authorizationError = error as? ASAuthorizationError,
           authorizationError.code == .canceled {
            errorMessage = nil
            return
        }

        if let authError = error as? AuthError {
            errorMessage = authError.userFacingMessage
            return
        }

        errorMessage = error.localizedDescription
    }
}
