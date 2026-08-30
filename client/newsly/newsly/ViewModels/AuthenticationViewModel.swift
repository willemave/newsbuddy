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
private let credentialClearFailureMessage =
    "Secure sign-in cleanup did not finish. Please try signing out again."

/// Authentication state
enum AuthState: Equatable {
    case loading
    case unauthenticated
    case authenticated(User)
}

/// Process authentication controller and sole owner of cached profile state.
@MainActor
@Observable
final class AuthenticationController {
    private enum AuthWorkKind {
        case idle
        case restoration
        case credentialReplacement
    }

    private struct PendingSessionEnd {
        let id: UUID
        let task: Task<Bool, Never>
    }

    var authState: AuthState = .loading
    var errorMessage: String?

    @ObservationIgnored
    private let authService: any AuthenticationServicing
    @ObservationIgnored
    private let userCache: any AuthenticatedUserCaching
    @ObservationIgnored
    private let credentialStorage: any CredentialMaterialStoring
    @ObservationIgnored
    private var lastKnownUser: User?
    @ObservationIgnored
    private var authWorkGeneration: UInt64 = 0
    @ObservationIgnored
    private var credentialIntentGeneration: UInt64 = 0
    @ObservationIgnored
    private var authWorkKind: AuthWorkKind = .idle
    @ObservationIgnored
    private var authWorkTask: Task<Void, Never>?
    @ObservationIgnored
    private var pendingSessionEnd: PendingSessionEnd?
    #if DEBUG
    @ObservationIgnored
    private var hasAttemptedE2EAutoLogin = false
    #endif

    init(
        authService: any AuthenticationServicing,
        tokenStore: any AuthTokenStore,
        userCache: (any AuthenticatedUserCaching)? = nil,
        credentialStorage: (any CredentialMaterialStoring)? = nil,
        credentialSession: CredentialSession? = nil
    ) {
        self.authService = authService
        self.userCache = userCache ?? KeychainAuthenticatedUserCache(tokenStore: tokenStore)
        self.credentialStorage = credentialStorage
            ?? CredentialStorageFactory.make(tokenStore: tokenStore)

        credentialSession?.setTerminalHandler { [weak self] event in
            Task { @MainActor [weak self] in
                self?.handleTerminalCredentialEvent(event)
            }
        }

        checkAuthStatus()
    }

    /// Check if user is already authenticated on app launch
    func checkAuthStatus() {
        errorMessage = nil

        #if DEBUG
        if E2ETestLaunch.shouldAutoLogin && !hasAttemptedE2EAutoLogin {
            hasAttemptedE2EAutoLogin = true
            authState = .loading
            let generation = beginAuthWork(
                .credentialReplacement,
                changesCredentialIdentity: true
            )
            performE2EAutoLogin(generation: generation)
            return
        }
        #endif

        let generation = beginAuthWork(.restoration)
        if let pendingClear = pendingSessionEnd?.task {
            authState = .loading
            authWorkTask = Task { [weak self] in
                _ = await pendingClear.value
                guard let self, self.canCommit(generation) else { return }
                self.prepareRestoration(generation: generation)
            }
            return
        }
        prepareRestoration(generation: generation)
    }

    private func prepareRestoration(generation: UInt64) {
        guard canCommit(generation) else { return }

        let existingUser = lastKnownUser ?? {
            guard case .authenticated(let user) = authState else { return nil }
            return user
        }()

        switch credentialStorage.credentialAvailability() {
        case .present:
            break
        case .unavailable:
            // A failed Keychain read is not proof that credentials were
            // removed. Preserve an already-established shell, or stay in the
            // restoring state until launch/restoration is retried.
            errorMessage = "Secure sign-in information is temporarily unavailable."
            if let existingUser {
                lastKnownUser = existingUser
                authState = .authenticated(existingUser)
            } else {
                authState = .loading
            }
            finishAuthWork(generation)
            return
        case .missing:
            userCache.clear()
            lastKnownUser = nil
            authState = .unauthenticated
            finishAuthWork(generation)
            return
        }

        let cachedUser = userCache.loadConfirmed()
        lastKnownUser = cachedUser
        authState = cachedUser.map(AuthState.authenticated) ?? .loading

        authWorkTask = Task { [weak self] in
            guard let self else { return }
            do {
                let user = try await authService.getCurrentUser()
                guard canCommit(generation) else { return }
                errorMessage = nil
                lastKnownUser = user
                userCache.save(user)
                authState = .authenticated(user)
            } catch {
                guard canCommit(generation) else { return }
                handleRestorationFailure(error)
            }
            finishAuthWork(generation)
        }
    }

    /// Sign in with Apple
    func signInWithApple() {
        let generation = beginAuthWork(
            .credentialReplacement,
            changesCredentialIdentity: true
        )
        authState = .loading
        errorMessage = nil
        let pendingClear = pendingSessionEnd?.task

        authWorkTask = Task { [weak self] in
            guard let self else { return }
            if let pendingClear {
                _ = await pendingClear.value
            }
            guard canCommit(generation) else { return }
            do {
                let session = try await authService.signInWithApple()
                guard canCommit(generation) else { return }
                errorMessage = nil
                lastKnownUser = session.user
                userCache.save(session.user)
                authState = .authenticated(session.user)
            } catch {
                guard canCommit(generation) else { return }
                presentAuthError(error)
                authState = .unauthenticated
            }
            finishAuthWork(generation)
        }
    }

    /// Logout current user
    func logout() {
        _ = beginAuthWork(.idle, changesCredentialIdentity: true)
        let logoutIntent = credentialIntentGeneration
        applyLoggedOutPresentationState()
        let endTask = startSessionEnd(matching: nil)
        Task { [weak self] in
            let didEnd = await endTask.value
            guard let self,
                  !didEnd,
                  self.credentialIntentGeneration == logoutIntent,
                  self.authState == .unauthenticated else {
                return
            }
            self.errorMessage = credentialClearFailureMessage
        }
    }

    private func applyLoggedOutPresentationState() {
        userCache.clear()
        lastKnownUser = nil
        errorMessage = nil
        authState = .unauthenticated
    }

    func updateUser(_ user: User) {
        let existingUserID = lastKnownUser?.id ?? {
            guard case .authenticated(let currentUser) = authState else { return nil }
            return currentUser.id
        }()
        _ = beginAuthWork(
            .idle,
            changesCredentialIdentity: existingUserID != user.id
        )
        lastKnownUser = user
        userCache.save(user)
        authState = .authenticated(user)
    }

    #if DEBUG
    func startDebugSession(userID: Int) {
        let generation = beginAuthWork(
            .credentialReplacement,
            changesCredentialIdentity: true
        )
        userCache.clear()
        lastKnownUser = nil
        errorMessage = nil
        authState = .loading
        let clearTask = startSessionEnd(matching: nil)

        authWorkTask = Task { [weak self] in
            guard let self else { return }
            _ = await clearTask.value
            guard canCommit(generation) else { return }
            do {
                let session = try await authService.createDebugSession(
                    userId: userID,
                    hasCompletedOnboarding: nil,
                    hasCompletedNewUserTutorial: nil
                )
                guard canCommit(generation) else { return }
                lastKnownUser = session.user
                userCache.save(session.user)
                authState = .authenticated(session.user)
            } catch {
                guard canCommit(generation) else { return }
                presentAuthError(error)
                authState = .unauthenticated
            }
            finishAuthWork(generation)
        }
    }
    #endif

    // MARK: - Private

    private func handleRestorationFailure(_ error: Error) {
        let failure = ClientFailure.classify(error)
        switch failure {
        case .authenticationRequired, .authenticationExpired:
            logout()
        case .cancelled:
            errorMessage = nil
            retainRestoringSession()
        case .connectivity:
            errorMessage = "We couldn't reach Newsbuddy. Check your connection and try again."
            retainRestoringSession()
        case .http(let statusCode, let detail):
            errorMessage = AuthError.serverError(
                statusCode: statusCode,
                message: detail
            ).userFacingMessage
            retainRestoringSession()
        case .invalidRequest, .invalidResponse, .decoding, .unexpected:
            errorMessage = (error as? AuthError)?.userFacingMessage
                ?? error.localizedDescription
            retainRestoringSession()
        }
    }

    func handleTerminalCredentialEvent(_ event: CredentialTerminalEvent) {
        authViewModelLogger.error(
            "[AuthState] Credential generation rejected | generation=\(event.generation, privacy: .public) user_id=\(event.userID.map(String.init) ?? "legacy", privacy: .public)"
        )
        let observedCredentialIntent = credentialIntentGeneration
        let shouldApplyToPresentation = authState != .unauthenticated
            && authWorkKind != .credentialReplacement
        let endTask = startSessionEnd(matching: event)
        Task { [weak self] in
            let didEnd = await endTask.value
            guard let self else { return }
            guard didEnd else {
                if shouldApplyToPresentation,
                   self.credentialIntentGeneration == observedCredentialIntent {
                    self.errorMessage = credentialClearFailureMessage
                }
                return
            }
            guard
                  shouldApplyToPresentation,
                  self.credentialIntentGeneration == observedCredentialIntent,
                  self.authWorkKind != .credentialReplacement else {
                return
            }
            _ = self.beginAuthWork(.idle, changesCredentialIdentity: true)
            self.applyLoggedOutPresentationState()
        }
    }

    #if DEBUG
    private func performE2EAutoLogin(generation: UInt64) {
        let clearTask = startSessionEnd(matching: nil)
        authWorkTask = Task { [weak self] in
            guard let self else { return }
            _ = await clearTask.value
            guard canCommit(generation) else { return }
            do {
                let session = try await authService.createDebugSession(
                    userId: E2ETestLaunch.userID,
                    hasCompletedOnboarding: E2ETestLaunch.completeOnboarding,
                    hasCompletedNewUserTutorial: E2ETestLaunch.completeTutorial
                )
                guard canCommit(generation) else { return }
                errorMessage = nil
                lastKnownUser = session.user
                userCache.save(session.user)
                authState = .authenticated(session.user)
            } catch {
                guard canCommit(generation) else { return }
                presentAuthError(error)
                authState = .unauthenticated
            }
            finishAuthWork(generation)
        }
    }
    #endif

    @discardableResult
    private func beginAuthWork(
        _ kind: AuthWorkKind,
        changesCredentialIdentity: Bool = false
    ) -> UInt64 {
        authWorkTask?.cancel()
        authWorkTask = nil
        authWorkGeneration &+= 1
        if changesCredentialIdentity {
            credentialIntentGeneration &+= 1
        }
        authWorkKind = kind
        return authWorkGeneration
    }

    private func canCommit(_ generation: UInt64) -> Bool {
        generation == authWorkGeneration && !Task.isCancelled
    }

    private func finishAuthWork(_ generation: UInt64) {
        guard generation == authWorkGeneration else { return }
        authWorkKind = .idle
        authWorkTask = nil
    }

    @discardableResult
    private func startSessionEnd(
        matching event: CredentialTerminalEvent?
    ) -> Task<Bool, Never> {
        let predecessor = pendingSessionEnd?.task
        let id = UUID()
        let task = Task { [authService] in
            if let predecessor {
                _ = await predecessor.value
            }
            return await authService.logout(matching: event)
        }
        pendingSessionEnd = PendingSessionEnd(id: id, task: task)
        Task { [weak self] in
            _ = await task.value
            guard let self, self.pendingSessionEnd?.id == id else { return }
            self.pendingSessionEnd = nil
        }
        return task
    }

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

    private func retainRestoringSession() {
        if let user = lastKnownUser {
            authState = .authenticated(user)
        } else {
            authState = .loading
        }
    }
}

/// Compatibility name for presentation code while the controller migration is
/// completed call site by call site.
typealias AuthenticationViewModel = AuthenticationController
