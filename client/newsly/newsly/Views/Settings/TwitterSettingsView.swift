//
//  TwitterSettingsView.swift
//  newsly
//

import SwiftUI

struct TwitterSettingsView: View {
    @EnvironmentObject private var authViewModel: AuthenticationViewModel
    @State private var showingAlert = false
    @State private var alertMessage = ""
    @State private var isUpdatingXConnection = false
    @State private var xConnection: XConnectionResponse?

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
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
            await loadAccountState()
        }
        .onChange(of: authViewModel.authState) { _, _ in
            Task { await loadAccountState() }
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
                .disabled(isUpdatingXConnection)
            } else {
                Button {
                    Task { await connectX() }
                } label: {
                    SettingsRow(
                        icon: "link.badge.plus",
                        iconColor: .brandPrimary,
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
