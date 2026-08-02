//
//  TwitterSettingsView.swift
//  newsly
//

import SwiftUI

struct TwitterSettingsView: View {
    @Environment(AuthenticationViewModel.self) private var authViewModel
    @State private var showingAlert = false
    @State private var alertMessage = ""
    @State private var isUpdatingXConnection = false
    @State private var showingConnectDisclosure = false
    @State private var showingDisconnectConfirmation = false
    @State private var xConnection: XConnectionResponse?

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                accountHeader

                if let xConnection, xConnection.needsAttention {
                    connectionIssueCard(connection: xConnection)
                }

                syncSection
                connectionSection
            }
            .padding(.bottom, 40)
        }
        .background(Color.surfacePrimary)
        .navigationTitle("X / Twitter")
        .navigationBarTitleDisplayMode(.inline)
        .alert("X / Twitter", isPresented: $showingAlert) {
            Button("OK", role: .cancel) { }
        } message: {
            Text(alertMessage)
        }
        .confirmationDialog(
            "Disconnect X?",
            isPresented: $showingDisconnectConfirmation,
            titleVisibility: .visible
        ) {
            Button("Disconnect", role: .destructive) {
                Task { await disconnectX() }
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("New bookmarks stop syncing. Posts already in your feed stay there.")
        }
        .alert("Connect X bookmark sync?", isPresented: $showingConnectDisclosure) {
            Button("Cancel", role: .cancel) { }
            Button("Continue to X") {
                Task { await connectX() }
            }
        } message: {
            Text("Newsbuddy will securely store your X authorization and import your bookmarks in the background about every 15 minutes. You can disconnect and revoke access here at any time.")
        }
        .task {
            await loadAccountState()
        }
        .onChange(of: authViewModel.authState) { _, _ in
            Task { await loadAccountState() }
        }
    }

    /// The account is the subject of this screen, so it leads instead of hiding in
    /// the subtitle of a destructive button.
    private var accountHeader: some View {
        VStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(isXConnected ? Color.onSurface : Color.surfaceTertiary)
                    .frame(width: 64, height: 64)

                Text("𝕏")
                    .font(.appSans(size: 28, weight: .semibold))
                    .foregroundStyle(isXConnected ? Color.surfacePrimary : Color.onSurfaceSecondary)
            }
            .accessibilityHidden(true)

            VStack(spacing: 4) {
                Text(accountTitle)
                    .font(.appTitle3.weight(.semibold))
                    .foregroundStyle(Color.onSurface)

                statusPill
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 24)
        .padding(.bottom, 4)
        .accessibilityElement(children: .combine)
    }

    private var statusPill: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(statusColor)
                .frame(width: 6, height: 6)

            Text(statusLabel)
                .font(.listCaption)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(Color.surfaceSecondary, in: Capsule())
    }

    private var syncSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Bookmark Sync")

            VStack(alignment: .leading, spacing: 0) {
                Text("Posts you bookmark on X are pulled in and read alongside the rest of your long-form feed.")
                    .font(.listCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, Spacing.rowHorizontal)
                    .padding(.vertical, Spacing.rowVertical)

                if isXConnected {
                    RowDivider(leadingInset: Spacing.rowHorizontal)

                    HStack(spacing: 12) {
                        Text("Last synced")
                            .font(.listTitle)
                            .foregroundStyle(Color.onSurface)

                        Spacer(minLength: 8)

                        Text(lastSyncedLabel)
                            .font(.listCaption)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                    .appRow(.compact)
                }
            }
            .settingsCard()
        }
    }

    private var connectionSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Connection")

            Group {
                if isXConnected {
                    Button {
                        showingDisconnectConfirmation = true
                    } label: {
                        // `link.badge.minus` does not exist in SF Symbols, so this
                        // row rendered with an empty icon slot.
                        SettingsRow(
                            icon: "at.badge.minus",
                            iconColor: .statusDestructive,
                            title: "Disconnect X"
                        ) {
                            if isUpdatingXConnection {
                                ProgressView()
                            } else {
                                EmptyView()
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(isUpdatingXConnection)
                } else {
                    Button {
                        showingConnectDisclosure = true
                    } label: {
                        SettingsRow(
                            icon: "link.badge.plus",
                            title: xConnection?.connectActionTitle ?? "Connect X",
                            subtitle: xConnection?.connectActionSubtitle
                                ?? "Authorize bookmark sync from your X account"
                        ) {
                            if isUpdatingXConnection {
                                ProgressView()
                            } else {
                                EmptyView()
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(isUpdatingXConnection)
                }
            }
            .settingsCard()
        }
    }

    private var isXConnected: Bool {
        xConnection?.connected == true
    }

    private var accountTitle: String {
        if let username = xConnection?.providerUsername, !username.isEmpty {
            return "@\(username)"
        }
        return "X / Twitter"
    }

    private var statusLabel: String {
        if let xConnection, xConnection.needsAttention {
            return xConnection.issueSummary
        }
        return isXConnected ? "Connected" : "Not connected"
    }

    private var statusColor: Color {
        if let xConnection, xConnection.needsAttention {
            return .statusDestructive
        }
        return isXConnected ? .statusSuccess : .statusInactive
    }

    private var lastSyncedLabel: String {
        ContentTimestampFormatter.detailMetaText(from: xConnection?.lastSyncedAt) ?? "Not yet"
    }

    private func connectionIssueCard(connection: XConnectionResponse) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.appSymbol(size: 15))
                .foregroundStyle(Color.statusDestructive)
                .padding(.top, 1)

            VStack(alignment: .leading, spacing: 4) {
                Text(connection.issueTitle)
                    .font(.listTitle.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                    .fixedSize(horizontal: false, vertical: true)

                Text(connection.issueMessage)
                    .font(.listCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .fixedSize(horizontal: false, vertical: true)

                if let details = connection.issueDetails {
                    Text(details)
                        .font(.appCaption)
                        .foregroundStyle(Color.statusDestructive)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Spacer(minLength: 0)
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, Spacing.rowVertical)
        .background(
            Color.statusDestructive.opacity(0.1),
            in: RoundedRectangle(cornerRadius: 14, style: .continuous)
        )
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, Spacing.sectionTop)
    }

    @MainActor
    private func loadAccountState() async {
        guard case .authenticated = authViewModel.authState else {
            xConnection = nil
            return
        }

        do {
            xConnection = try await XIntegrationService.shared.fetchConnection()
        } catch {
            xConnection = nil
        }
    }

    @MainActor
    private func connectX() async {
        guard !isUpdatingXConnection else { return }
        isUpdatingXConnection = true
        defer { isUpdatingXConnection = false }

        do {
            _ = try await XIntegrationService.shared.connectViaOAuth()
            let user = try await AuthenticationService.shared.getCurrentUser()
            authViewModel.updateUser(user)
            await loadAccountState()
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
            await loadAccountState()
            alertMessage = "X disconnected."
            showingAlert = true
        } catch {
            alertMessage = "Failed to disconnect X: \(error.localizedDescription)"
            showingAlert = true
        }
    }
}
