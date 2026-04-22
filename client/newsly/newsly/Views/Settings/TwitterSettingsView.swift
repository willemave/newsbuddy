//
//  TwitterSettingsView.swift
//  newsly
//

import SwiftUI

struct TwitterSettingsView: View {
    @EnvironmentObject private var authViewModel: AuthenticationViewModel
    @State private var showingAlert = false
    @State private var alertMessage = ""
    @State private var isSavingTwitterUsername = false
    @State private var isUpdatingXConnection = false
    @State private var twitterUsernameDraft = ""
    @State private var serverTwitterUsername = ""
    @State private var hasUnsavedTwitterUsernameEdits = false
    @State private var xConnection: XConnectionResponse?
    @State private var hasLoadedAccountState = false
    @FocusState private var isTwitterUsernameFieldFocused: Bool

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                identitySection
                SectionDivider()
                connectionSection
                Spacer(minLength: 40)
            }
        }
        .background(Color.surfacePrimary)
        .navigationTitle("X / Twitter")
        .navigationBarTitleDisplayMode(.inline)
        .alert("X / Twitter", isPresented: $showingAlert) {
            Button("OK", role: .cancel) { }
        } message: {
            Text(alertMessage)
        }
        .task {
            await loadAccountState(force: true)
        }
        .onChange(of: authViewModel.authState) { _, _ in
            Task { await loadAccountState(force: true) }
        }
    }

    private var identitySection: some View {
        VStack(spacing: 0) {
            SectionHeader(title: "Identity")
            usernameInputRow
            RowDivider()
            Button {
                Task { await saveTwitterUsername() }
            } label: {
                SettingsRow(
                    icon: "person.text.rectangle",
                    iconColor: .blue,
                    title: "Save Username",
                    subtitle: "Used for bookmark sync and shared tweet metadata"
                ) {
                    if isSavingTwitterUsername {
                        ProgressView()
                    } else {
                        EmptyView()
                    }
                }
            }
            .buttonStyle(.plain)
            .disabled(isSavingTwitterUsername || isUpdatingXConnection || !hasUnsavedTwitterUsernameEdits)
        }
    }

    private var connectionSection: some View {
        VStack(spacing: 0) {
            SectionHeader(title: "Connection")

            if let xConnection, xConnection.needsAttention {
                connectionIssueCard(connection: xConnection)
                SectionDivider()
            }

            if isXConnected {
                Button {
                    Task { await disconnectX() }
                } label: {
                    SettingsRow(
                        icon: "link.badge.minus",
                        iconColor: .statusDestructive,
                        title: "Disconnect X",
                        subtitle: xConnectionSubtitle
                    ) {
                        if isUpdatingXConnection {
                            ProgressView()
                        } else {
                            EmptyView()
                        }
                    }
                }
                .buttonStyle(.plain)
                .disabled(isSavingTwitterUsername || isUpdatingXConnection)
            } else {
                Button {
                    Task { await connectX() }
                } label: {
                    SettingsRow(
                        icon: "link.badge.plus",
                        iconColor: .green,
                        title: xConnection?.connectActionTitle ?? "Connect X",
                        subtitle: xConnection?.connectActionSubtitle
                            ?? "Authorize bookmarks, follows, and lists from your X account"
                    ) {
                        if isUpdatingXConnection {
                            ProgressView()
                        } else {
                            EmptyView()
                        }
                    }
                }
                .buttonStyle(.plain)
                .disabled(isSavingTwitterUsername || isUpdatingXConnection)
            }
        }
    }

    private var usernameInputRow: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Username")
                .font(.listCaption)
                .foregroundStyle(Color.onSurfaceSecondary)
            TextField("@username", text: $twitterUsernameDraft)
                .textInputAutocapitalization(.never)
                .disableAutocorrection(true)
                .focused($isTwitterUsernameFieldFocused)
                .onChange(of: twitterUsernameDraft) { _, newValue in
                    hasUnsavedTwitterUsernameEdits =
                        normalizedTwitterUsernameForComparison(newValue)
                        != normalizedTwitterUsernameForComparison(serverTwitterUsername)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Color.surfaceSecondary)
                .clipShape(RoundedRectangle(cornerRadius: 10))
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, Spacing.rowVertical)
    }

    private var authenticatedUser: User? {
        guard case .authenticated(let user) = authViewModel.authState else {
            return nil
        }
        return user
    }

    private var isXConnected: Bool {
        xConnection?.connected == true
    }

    private var xConnectionSubtitle: String {
        if let username = xConnection?.providerUsername, !username.isEmpty {
            return "@\(username)"
        }
        if let subtitle = xConnection?.settingsSubtitle, !subtitle.isEmpty {
            return subtitle
        }
        return "Connected"
    }

    @ViewBuilder
    private func connectionIssueCard(connection: XConnectionResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(Color.statusDestructive)
                    .padding(.top, 1)

                VStack(alignment: .leading, spacing: 4) {
                    Text(connection.issueTitle)
                        .font(.listTitle.weight(.semibold))
                        .foregroundStyle(Color.onSurface)

                    Text(connection.issueMessage)
                        .font(.listCaption)
                        .foregroundStyle(Color.onSurfaceSecondary)

                    if let details = connection.issueDetails {
                        Text(details)
                            .font(.caption)
                            .foregroundStyle(Color.statusDestructive)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, Spacing.rowVertical)
        .background(
            Color.statusDestructive.opacity(0.1),
            in: RoundedRectangle(cornerRadius: 12, style: .continuous)
        )
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, 12)
    }

    private func normalizedTwitterUsernameDraft() -> String? {
        let trimmed = twitterUsernameDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        if trimmed.hasPrefix("@") {
            return String(trimmed.dropFirst())
        }
        return trimmed
    }

    private func normalizedTwitterUsernameForComparison(_ value: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        let withoutPrefix = trimmed.hasPrefix("@") ? String(trimmed.dropFirst()) : trimmed
        return withoutPrefix.lowercased()
    }

    @MainActor
    private func loadAccountState(force: Bool) async {
        guard let user = authenticatedUser else {
            xConnection = nil
            twitterUsernameDraft = ""
            serverTwitterUsername = ""
            hasUnsavedTwitterUsernameEdits = false
            hasLoadedAccountState = false
            return
        }

        let userUsername = user.twitterUsername ?? ""
        if !hasLoadedAccountState {
            serverTwitterUsername = userUsername
            twitterUsernameDraft = userUsername
            hasUnsavedTwitterUsernameEdits = false
            hasLoadedAccountState = true
        } else if force {
            serverTwitterUsername = userUsername
            if !isTwitterUsernameFieldFocused && !hasUnsavedTwitterUsernameEdits {
                twitterUsernameDraft = userUsername
            }
        }

        do {
            xConnection = try await XIntegrationService.shared.fetchConnection()
            let resolvedUsername = userUsername.isEmpty
                ? (xConnection?.twitterUsername ?? "")
                : userUsername
            serverTwitterUsername = resolvedUsername
            if !isTwitterUsernameFieldFocused && !hasUnsavedTwitterUsernameEdits {
                twitterUsernameDraft = resolvedUsername
            }
        } catch {
            xConnection = nil
            serverTwitterUsername = userUsername
            if !isTwitterUsernameFieldFocused && !hasUnsavedTwitterUsernameEdits {
                twitterUsernameDraft = userUsername
            }
        }
        hasUnsavedTwitterUsernameEdits =
            normalizedTwitterUsernameForComparison(twitterUsernameDraft)
            != normalizedTwitterUsernameForComparison(serverTwitterUsername)
    }

    @MainActor
    private func saveTwitterUsername() async {
        guard !isSavingTwitterUsername, authenticatedUser != nil else { return }
        isSavingTwitterUsername = true
        defer { isSavingTwitterUsername = false }

        do {
            let user = try await AuthenticationService.shared.updateCurrentUserProfile(
                twitterUsername: normalizedTwitterUsernameDraft()
            )
            authViewModel.updateUser(user)
            serverTwitterUsername = user.twitterUsername ?? ""
            twitterUsernameDraft = serverTwitterUsername
            hasUnsavedTwitterUsernameEdits = false
            alertMessage = "Username saved."
            showingAlert = true
            await loadAccountState(force: true)
        } catch {
            alertMessage = "Failed to save username: \(error.localizedDescription)"
            showingAlert = true
        }
    }

    @MainActor
    private func connectX() async {
        guard !isUpdatingXConnection else { return }
        isUpdatingXConnection = true
        defer { isUpdatingXConnection = false }

        do {
            _ = try await XIntegrationService.shared.connectViaOAuth(
                twitterUsername: normalizedTwitterUsernameDraft()
            )
            let user = try await AuthenticationService.shared.getCurrentUser()
            authViewModel.updateUser(user)
            serverTwitterUsername = user.twitterUsername ?? ""
            twitterUsernameDraft = serverTwitterUsername
            hasUnsavedTwitterUsernameEdits = false
            await loadAccountState(force: true)
            alertMessage = "X connected successfully."
            showingAlert = true
        } catch {
            alertMessage = "Failed to connect X: \(error.localizedDescription)"
            showingAlert = true
        }
    }

    @MainActor
    private func disconnectX() async {
        guard !isUpdatingXConnection else { return }
        isUpdatingXConnection = true
        defer { isUpdatingXConnection = false }

        do {
            try await XIntegrationService.shared.disconnect()
            let user = try await AuthenticationService.shared.getCurrentUser()
            authViewModel.updateUser(user)
            serverTwitterUsername = user.twitterUsername ?? ""
            if !hasUnsavedTwitterUsernameEdits {
                twitterUsernameDraft = serverTwitterUsername
            }
            await loadAccountState(force: true)
            alertMessage = "X disconnected."
            showingAlert = true
        } catch {
            alertMessage = "Failed to disconnect X: \(error.localizedDescription)"
            showingAlert = true
        }
    }
}
