//
//  AuthenticationViewModel.swift
//  newsly
//
//  Created by Assistant on 10/25/25.
//

import Foundation
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
final class AuthenticationViewModel: ObservableObject {
    @Published var authState: AuthState = .loading
    @Published var errorMessage: String?

    private let authService = AuthenticationService.shared
    private var lastKnownUser: User?

    init() {
        checkAuthStatus()

        // Listen for authentication required notifications
        NotificationCenter.default.addObserver(
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

    /// Check if user is already authenticated on app launch
    func checkAuthStatus() {
        authState = .loading

        let hasRefreshToken = KeychainManager.shared.getToken(key: .refreshToken) != nil
        let hasAccessToken = KeychainManager.shared.getToken(key: .accessToken) != nil

        // No tokens at all -> user must sign in
        guard hasRefreshToken || hasAccessToken else {
            authState = .unauthenticated
            return
        }

        Task {
            do {
                let user = try await authService.getCurrentUser()
                lastKnownUser = user
                authState = .authenticated(user)
                await syncNewsDigestTimezoneIfNeeded(for: user)
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
                lastKnownUser = session.user
                authState = .authenticated(session.user)
                await syncNewsDigestTimezoneIfNeeded(for: session.user)
            } catch {
                errorMessage = error.localizedDescription
                authState = .unauthenticated
            }
        }
    }

    /// Logout current user
    func logout() {
        authService.logout()
        lastKnownUser = nil
        authState = .unauthenticated
    }

    func updateUser(_ user: User) {
        lastKnownUser = user
        authState = .authenticated(user)
    }

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
            errorMessage = underlying.localizedDescription
            // Keep tokens; allow retry without forcing logout
            if let user = lastKnownUser {
                authState = .authenticated(user)
            } else {
                authState = .unauthenticated
            }
        case .serverError(_, let message):
            errorMessage = message
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
            lastKnownUser = user
            authState = .authenticated(user)
            await syncNewsDigestTimezoneIfNeeded(for: user)
            print("✅ User authenticated successfully after refresh")
        } catch let authError as AuthError {
            switch authError {
            case .refreshTokenExpired, .noRefreshToken:
                authService.logout()
                authState = .unauthenticated
            case .networkError(let underlying):
                errorMessage = underlying.localizedDescription
                if let user = lastKnownUser {
                    authState = .authenticated(user)
                } else {
                    authState = .unauthenticated
                }
            case .serverError(_, let message):
                errorMessage = message
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

    private func syncNewsDigestTimezoneIfNeeded(for user: User) async {
        let deviceTimezone = TimeZone.current.identifier
        guard !deviceTimezone.isEmpty else { return }
        guard user.newsDigestTimezone != deviceTimezone else { return }

        do {
            let updatedUser = try await authService.updateCurrentUserProfile(
                newsDigestTimezone: deviceTimezone
            )
            lastKnownUser = updatedUser
            authState = .authenticated(updatedUser)
        } catch {
            authViewModelLogger.warning(
                "[AuthState] Failed to sync digest timezone | current=\(user.newsDigestTimezone, privacy: .public) target=\(deviceTimezone, privacy: .public) error=\(error.localizedDescription, privacy: .public)"
            )
        }
    }
}
