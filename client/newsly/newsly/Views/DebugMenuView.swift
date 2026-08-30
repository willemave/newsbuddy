//
//  DebugMenuView.swift
//  newsly
//
//  Debug menu for testing authentication without Apple Sign In
//

import SwiftUI
import UIKit

#if DEBUG
struct DebugMenuView: View {
    @Environment(\.dismiss) var dismiss
    @Environment(AuthenticationViewModel.self) private var authViewModel
    @State private var appSettings = AppSettings.shared
    @State private var showingTokenInput = false
    @State private var forceOnboardingAfterTokenSave = false
    @State private var accessToken = ""
    @State private var refreshToken = ""
    @State private var localUserID = ""
    @State private var showingAlert = false
    @State private var alertMessage = ""
    @State private var suppressNextAuthenticatedDismiss = false

    var body: some View {
        NavigationStack {
            List {
                Section(header: Text("Server Configuration")) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Current Endpoint")
                            .font(.appCaption)
                            .foregroundColor(Color.onSurfaceSecondary)
                        Text(appSettings.baseURL)
                            .font(.listValue)
                            .foregroundColor(.brandSecondary)
                            .textSelection(.enabled)
                    }

                    HStack {
                        Text("Host")
                        TextField("localhost", text: $appSettings.serverHost)
                            .multilineTextAlignment(.trailing)
                            .foregroundColor(Color.onSurface)
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                            .padding(.horizontal, 12)
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .trailing)
                            .background(Color.surfaceSecondary)
                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                            .overlay(
                                RoundedRectangle(cornerRadius: 10, style: .continuous)
                                    .stroke(Color.outlineVariant.opacity(0.5), lineWidth: 1)
                            )
                            .accessibilityLabel("Host")
                    }
                    .frame(minHeight: 52)

                    HStack {
                        Text("Port")
                        TextField("8000", text: $appSettings.serverPort)
                            .multilineTextAlignment(.trailing)
                            .foregroundColor(Color.onSurface)
                            .keyboardType(.numberPad)
                            .padding(.horizontal, 12)
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .trailing)
                            .background(Color.surfaceSecondary)
                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                            .overlay(
                                RoundedRectangle(cornerRadius: 10, style: .continuous)
                                    .stroke(Color.outlineVariant.opacity(0.5), lineWidth: 1)
                            )
                            .accessibilityLabel("Port")
                    }
                    .frame(minHeight: 52)

                    Toggle("Use HTTPS", isOn: $appSettings.useHTTPS)
                }

                Section(header: Text("Auth Status")) {
                    HStack {
                        Text("Auth State")
                        Spacer()
                        authStateText
                    }

                    HStack {
                        Text("User ID")
                        Spacer()
                        userIdText
                    }

                    HStack {
                        Text("Access Token")
                        Spacer()
                        if KeychainManager.shared.getToken(key: .accessToken) != nil {
                            Text("Stored ✓").foregroundColor(.brandSecondary)
                        } else {
                            Text("None").foregroundColor(.statusDestructive)
                        }
                    }

                    HStack {
                        Text("Refresh Token")
                        Spacer()
                        if KeychainManager.shared.getToken(key: .refreshToken) != nil {
                            Text("Stored ✓").foregroundColor(.brandSecondary)
                        } else {
                            Text("None").foregroundColor(.statusDestructive)
                        }
                    }
                }

                Section(header: Text("Local User")) {
                    TextField("User ID", text: $localUserID)
                        .keyboardType(.numberPad)
                        .accessibilityLabel("Local user ID")

                    Button("Sign In as Local User") {
                        signInAsLocalUser()
                    }
                    .disabled(parsedLocalUserID == nil)

                    Button("Copy Debug Context") {
                        copyDebugContext()
                    }
                }

                Section(header: Text("Actions")) {
                    Button("Sign In with Stored Token") {
                        signInWithStoredToken()
                    }
                    .disabled(KeychainManager.shared.getToken(key: .accessToken) == nil)

                    Button("Set Tokens") {
                        forceOnboardingAfterTokenSave = false
                        showingTokenInput = true
                    }

                    Button("Create New Onboarding User") {
                        createNewOnboardingUser()
                    }

                    Button("Reset Current User Onboarding") {
                        resetCurrentUserOnboarding()
                    }
                    .disabled(currentUser == nil)

                    Button("Reset Auth (Clear Tokens)") {
                        resetAuth()
                    }
                    .foregroundColor(.statusDestructive)
                }
            }
            .navigationTitle("🐛 Debug Menu")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") {
                        dismiss()
                    }
                }
            }
        }
        .onChange(of: authViewModel.authState) { _, newValue in
            // Auto-dismiss when authentication succeeds
            if case .authenticated = newValue {
                if suppressNextAuthenticatedDismiss {
                    suppressNextAuthenticatedDismiss = false
                    return
                }
                dismiss()
            }
        }
        .sheet(isPresented: $showingTokenInput, onDismiss: {
            forceOnboardingAfterTokenSave = false
        }) {
            TokenInputView(
                accessToken: $accessToken,
                refreshToken: $refreshToken,
                forceOnboardingAfterSave: $forceOnboardingAfterTokenSave,
                onSave: {
                    saveTokensManually()
                }
            )
        }
        .alert("Debug Action", isPresented: $showingAlert) {
            Button("OK") { }
        } message: {
            Text(alertMessage)
        }
    }

    private var authStateText: some View {
        switch authViewModel.authState {
        case .loading:
            return Text("Loading...").foregroundColor(.brandPrimary)
        case .unauthenticated:
            return Text("Unauthenticated").foregroundColor(.statusDestructive)
        case .authenticated(let user):
            return Text("Authenticated: \(user.email)").foregroundColor(.brandSecondary)
        }
    }

    private var userIdText: some View {
        switch authViewModel.authState {
        case .authenticated(let user):
            return Text("\(user.id)").foregroundColor(Color.onSurface)
        case .loading:
            return Text("—").foregroundColor(Color.onSurfaceSecondary)
        case .unauthenticated:
            return Text("—").foregroundColor(Color.onSurfaceSecondary)
        }
    }

    private var currentUser: User? {
        if case .authenticated(let user) = authViewModel.authState {
            return user
        }
        return nil
    }

    private var parsedLocalUserID: Int? {
        guard let value = Int(localUserID), value > 0 else {
            return nil
        }
        return value
    }

    private func signInAsLocalUser() {
        guard let userID = parsedLocalUserID else {
            return
        }
        authViewModel.startDebugSession(userID: userID)
    }

    private func copyDebugContext() {
        let userDescription = currentUser.map { "\($0.id) \($0.email)" } ?? "unauthenticated"
        UIPasteboard.general.string = "endpoint=\(appSettings.baseURL) user=\(userDescription)"
        alertMessage = "Copied endpoint and authenticated user to the clipboard."
        showingAlert = true
    }

    private func signInWithStoredToken() {
        guard KeychainManager.shared.getToken(key: .accessToken) != nil else {
            alertMessage = "No access token found in keychain"
            showingAlert = true
            return
        }

        // Validate token with backend
        Task {
            do {
                authViewModel.authState = .loading
                let user = try await AuthenticationService.shared.getCurrentUser()
                await MainActor.run {
                    authViewModel.authState = .authenticated(user)
                }
            } catch {
                await MainActor.run {
                    authViewModel.authState = .unauthenticated
                    alertMessage = "Token is invalid or expired: \(error.localizedDescription)"
                    showingAlert = true
                }
            }
        }
    }

    private func saveTokensManually() {
        let normalizedAccessToken = normalizeManualToken(accessToken)
        let normalizedRefreshToken = normalizeManualToken(refreshToken)

        guard !normalizedAccessToken.isEmpty else {
            alertMessage = "Access token required"
            showingAlert = true
            return
        }
        guard !normalizedRefreshToken.isEmpty else {
            alertMessage = "Refresh token required"
            showingAlert = true
            return
        }

        showingTokenInput = false

        let shouldResetOnboarding = forceOnboardingAfterTokenSave

        // Validate token with backend
        Task {
            do {
                authViewModel.authState = .loading
                try await CredentialSession.shared.publishLegacyCandidate(
                    tokens: CredentialTokens(
                        accessToken: normalizedAccessToken,
                        refreshToken: normalizedRefreshToken
                    )
                )
                let user = try await AuthenticationService.shared.getCurrentUser()

                if shouldResetOnboarding {
                    do {
                        let resetUser = try await resetOnboardingSession(for: user)
                        await MainActor.run {
                            applyOnboardingDebugSession(user: resetUser)
                            forceOnboardingAfterTokenSave = false
                        }
                    } catch {
                        await MainActor.run {
                            forceOnboardingAfterTokenSave = false
                            presentDebugSessionFailure(
                                error,
                                fallbackUser: user,
                                action: "reset onboarding for this token"
                            )
                        }
                    }
                } else {
                    await MainActor.run {
                        authViewModel.authState = .authenticated(user)
                        forceOnboardingAfterTokenSave = false
                    }
                }
            } catch {
                await MainActor.run {
                    // Clear invalid token
                    KeychainManager.shared.clearAll()
                    authViewModel.authState = .unauthenticated
                    alertMessage = "Token is invalid or expired. Please generate a new one."
                    showingAlert = true
                    forceOnboardingAfterTokenSave = false
                }
            }
        }
    }

    private func normalizeManualToken(_ input: String) -> String {
        let trimmed = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return ""
        }

        let firstLine = trimmed
            .split(whereSeparator: \.isNewline)
            .first
            .map(String.init)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? trimmed

        let value: String
        if let separator = firstLine.firstIndex(of: "=") {
            value = String(firstLine[firstLine.index(after: separator)...])
        } else {
            value = firstLine
        }

        return value.trimmingCharacters(in: CharacterSet(charactersIn: "\"' ").union(.whitespacesAndNewlines))
    }

    private func createNewOnboardingUser() {
        let previousUser = currentUser

        Task {
            await MainActor.run {
                authViewModel.authState = .loading
            }

            do {
                let session = try await AuthenticationService.shared.createDebugSession(
                    hasCompletedOnboarding: false,
                    hasCompletedNewUserTutorial: false
                )
                if let previousUser {
                    OnboardingStateStore.shared.clearProgress(userId: previousUser.id)
                }
                await MainActor.run {
                    applyOnboardingDebugSession(user: session.user)
                }
            } catch {
                await MainActor.run {
                    presentDebugSessionFailure(
                        error,
                        fallbackUser: previousUser,
                        action: "create onboarding debug user"
                    )
                }
            }
        }
    }

    private func resetCurrentUserOnboarding() {
        guard let user = currentUser else {
            alertMessage = "Sign in before resetting onboarding for the current user."
            showingAlert = true
            return
        }

        Task {
            await MainActor.run {
                authViewModel.authState = .loading
            }

            do {
                let resetUser = try await resetOnboardingSession(for: user)
                await MainActor.run {
                    applyOnboardingDebugSession(user: resetUser)
                }
            } catch {
                await MainActor.run {
                    presentDebugSessionFailure(
                        error,
                        fallbackUser: user,
                        action: "reset current user onboarding"
                    )
                }
            }
        }
    }

    private func resetOnboardingSession(for user: User) async throws -> User {
        let session = try await AuthenticationService.shared.createDebugSession(
            userId: user.id,
            hasCompletedOnboarding: false,
            hasCompletedNewUserTutorial: false
        )
        return session.user
    }

    @MainActor
    private func applyOnboardingDebugSession(user: User) {
        OnboardingStateStore.shared.clearProgress(userId: user.id)
        authViewModel.authState = .authenticated(user)
    }

    @MainActor
    private func presentDebugSessionFailure(
        _ error: Error,
        fallbackUser: User?,
        action: String
    ) {
        if let fallbackUser {
            let fallbackState = AuthState.authenticated(fallbackUser)
            if authViewModel.authState != fallbackState {
                suppressNextAuthenticatedDismiss = true
            }
            authViewModel.authState = .authenticated(fallbackUser)
        } else {
            authViewModel.authState = .unauthenticated
        }

        if let authError = error as? AuthError {
            switch authError {
            case .serverError(let statusCode, _) where statusCode == 404:
                alertMessage = "Debug onboarding is disabled on this server. Enable DEBUG=true or run with ENVIRONMENT=development."
            default:
                alertMessage = "Failed to \(action): \(authError.localizedDescription)"
            }
        } else {
            alertMessage = "Failed to \(action): \(error.localizedDescription)"
        }
        showingAlert = true
    }

    private func resetAuth() {
        KeychainManager.shared.clearAll()
        SharedContainer.userDefaults.removeObject(forKey: "accessToken")
        SharedContainer.userDefaults.removeObject(forKey: "refreshToken")
        authViewModel.logout()
        authViewModel.authState = .unauthenticated
        alertMessage = "Cleared tokens and signed out"
        showingAlert = true
    }
}
#endif

struct TokenInputView: View {
    @Environment(\.dismiss) var dismiss
    @Binding var accessToken: String
    @Binding var refreshToken: String
    @Binding var forceOnboardingAfterSave: Bool
    let onSave: () -> Void

    var body: some View {
        NavigationStack {
            Form {
                Section(header: Text("Access Token (Required)")) {
                    TextEditor(text: $accessToken)
                        .frame(height: 100)
                        .font(.listValue)
                        .accessibilityLabel("Access token")
                }

                Section(header: Text("Refresh Token (Optional)")) {
                    TextEditor(text: $refreshToken)
                        .frame(height: 100)
                        .font(.listValue)
                        .accessibilityLabel("Refresh token")
                }

                Section {
                    Toggle("Reset onboarding after sign-in", isOn: $forceOnboardingAfterSave)
                }

                Section {
                    Button("Save Tokens") {
                        onSave()
                    }
                    .frame(maxWidth: .infinity)
                    .disabled(accessToken.isEmpty)
                }
            }
            .navigationTitle("Enter Tokens")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
            }
        }
    }
}
