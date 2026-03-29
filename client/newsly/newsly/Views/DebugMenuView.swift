//
//  DebugMenuView.swift
//  newsly
//
//  Debug menu for testing authentication without Apple Sign In
//

import SwiftUI

struct DebugMenuView: View {
    @Environment(\.dismiss) var dismiss
    @EnvironmentObject var authViewModel: AuthenticationViewModel
    @ObservedObject private var appSettings = AppSettings.shared
    @State private var showingTokenInput = false
    @State private var forceOnboardingAfterTokenSave = false
    @State private var accessToken = ""
    @State private var refreshToken = ""
    @State private var showingAlert = false
    @State private var alertMessage = ""

    var body: some View {
        NavigationStack {
            List {
                Section(header: Text("Server Configuration")) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Current Endpoint")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Text(appSettings.baseURL)
                            .font(.system(.caption, design: .monospaced))
                            .foregroundColor(.blue)
                            .textSelection(.enabled)
                    }

                    HStack {
                        Text("Host")
                        TextField("localhost", text: $appSettings.serverHost)
                            .multilineTextAlignment(.trailing)
                            .foregroundColor(.primary)
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                    }

                    HStack {
                        Text("Port")
                        TextField("8000", text: $appSettings.serverPort)
                            .multilineTextAlignment(.trailing)
                            .foregroundColor(.primary)
                            .keyboardType(.numberPad)
                    }

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
                            Text("Stored ✓").foregroundColor(.green)
                        } else {
                            Text("None").foregroundColor(.red)
                        }
                    }

                    HStack {
                        Text("Refresh Token")
                        Spacer()
                        if KeychainManager.shared.getToken(key: .refreshToken) != nil {
                            Text("Stored ✓").foregroundColor(.green)
                        } else {
                            Text("None").foregroundColor(.red)
                        }
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

                    Button("Force Onboarding (New User)") {
                        forceOnboarding()
                    }

                    Button("Reset Auth (Clear Tokens)") {
                        resetAuth()
                    }
                    .foregroundColor(.red)
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
        .onChange(of: authViewModel.authState) { oldValue, newValue in
            // Auto-dismiss when authentication succeeds
            if case .authenticated = newValue {
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
            return Text("Loading...").foregroundColor(.orange)
        case .unauthenticated:
            return Text("Unauthenticated").foregroundColor(.red)
        case .authenticated(let user):
            return Text("Authenticated: \(user.email)").foregroundColor(.green)
        }
    }

    private var userIdText: some View {
        switch authViewModel.authState {
        case .authenticated(let user):
            return Text("\(user.id)").foregroundColor(.primary)
        case .loading:
            return Text("—").foregroundColor(.secondary)
        case .unauthenticated:
            return Text("—").foregroundColor(.secondary)
        }
    }

    private var currentUser: User? {
        if case .authenticated(let user) = authViewModel.authState {
            return user
        }
        return nil
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
        guard !accessToken.isEmpty else {
            alertMessage = "Access token required"
            showingAlert = true
            return
        }

        // Save tokens to keychain
        KeychainManager.shared.saveToken(accessToken, key: .accessToken)
        // Also save to shared UserDefaults for extension access
        SharedContainer.userDefaults.set(accessToken, forKey: "accessToken")
        SharedContainer.userDefaults.synchronize()  // Force sync to disk
        print("🔐 [Main] Saved token to SharedDefaults (group: \(SharedContainer.appGroupId ?? "nil"))")
        print("🔐 [Main] Verify read back: \(SharedContainer.userDefaults.string(forKey: "accessToken")?.prefix(20) ?? "nil")...")
        // Debug: Print container path
        if let groupId = SharedContainer.appGroupId {
            let containerURL = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: groupId)
            print("🔐 [Main] Container URL: \(containerURL?.path ?? "nil")")
        }

        if !refreshToken.isEmpty {
            KeychainManager.shared.saveToken(refreshToken, key: .refreshToken)
        }

        showingTokenInput = false

        // Validate token with backend
        Task {
            do {
                authViewModel.authState = .loading
                let user = try await AuthenticationService.shared.getCurrentUser()
                await MainActor.run {
                    if forceOnboardingAfterTokenSave {
                        triggerForcedOnboarding(user: user)
                    } else {
                        authViewModel.authState = .authenticated(user)
                    }
                    forceOnboardingAfterTokenSave = false
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

    private func forceOnboarding() {
        let previousUserId = currentUser?.id
        let previousUser = currentUser

        Task {
            do {
                let session = try await AuthenticationService.shared.createDebugUser()
                if let previousUserId {
                    OnboardingStateStore.shared.clearDiscoveryRun(userId: previousUserId)
                }
                await MainActor.run {
                    triggerForcedOnboarding(user: session.user)
                }
            } catch let authError as AuthError {
                await MainActor.run {
                    switch authError {
                    case .serverError(let statusCode, _) where statusCode == 404:
                        if let previousUser {
                            authViewModel.authState = .authenticated(previousUser)
                        } else {
                            authViewModel.authState = .unauthenticated
                        }
                        alertMessage = "Debug new-user endpoint is disabled on this server. Enable DEBUG=true or run with ENVIRONMENT=development."
                        showingAlert = true
                    default:
                        if let previousUser {
                            authViewModel.authState = .authenticated(previousUser)
                        } else {
                            authViewModel.authState = .unauthenticated
                        }
                        alertMessage = "Failed to create debug user: \(authError.localizedDescription)"
                        showingAlert = true
                    }
                }
            } catch {
                await MainActor.run {
                    if let previousUser {
                        authViewModel.authState = .authenticated(previousUser)
                    } else {
                        authViewModel.authState = .unauthenticated
                    }
                    alertMessage = "Failed to create debug user: \(error.localizedDescription)"
                    showingAlert = true
                }
            }
        }
    }

    @MainActor
    private func triggerForcedOnboarding(user: User) {
        OnboardingStateStore.shared.clearDiscoveryRun(userId: user.id)
        authViewModel.authState = .authenticated(userWithResetOnboardingFlags(user))
    }

    private func userWithResetOnboardingFlags(_ user: User) -> User {
        User(
            id: user.id,
            appleId: user.appleId,
            email: user.email,
            fullName: user.fullName,
            twitterUsername: user.twitterUsername,
            newsDigestPreferencePrompt: user.newsDigestPreferencePrompt,
            newsDigestTimezone: user.newsDigestTimezone,
            newsDigestIntervalHours: user.newsDigestIntervalHours,
            hasXBookmarkSync: user.hasXBookmarkSync,
            isAdmin: user.isAdmin,
            isActive: user.isActive,
            hasCompletedOnboarding: false,
            hasCompletedNewUserTutorial: false,
            hasCompletedLiveVoiceOnboarding: user.hasCompletedLiveVoiceOnboarding,
            createdAt: user.createdAt,
            updatedAt: user.updatedAt
        )
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
                        .font(.system(.caption, design: .monospaced))
                }

                Section(header: Text("Refresh Token (Optional)")) {
                    TextEditor(text: $refreshToken)
                        .frame(height: 100)
                        .font(.system(.caption, design: .monospaced))
                }

                Section {
                    Toggle("Force onboarding after sign-in", isOn: $forceOnboardingAfterSave)
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
