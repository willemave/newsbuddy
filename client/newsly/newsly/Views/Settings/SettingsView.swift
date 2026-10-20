//
//  SettingsView.swift
//  newsly
//

import SwiftUI
import UIKit
import AuthenticationServices

private enum SettingsSheetDestination: String, Identifiable {
    #if DEBUG && targetEnvironment(simulator)
    case debugMenu
    #endif
    case cliLinkScanner
    case feedback

    var id: String { rawValue }
}

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AuthenticationController.self) private var authViewModel
    @Environment(BadgeStatsStore.self) private var badgeStatsStore
    @Environment(XConnectionStore.self) private var xConnectionStore
    @Environment(RootDependencyFactory.self) private var dependencyFactory
    private let showsDismissButton: Bool
    @State private var showingAlert = false
    @State private var alertMessage = ""
    @State private var showMarkAllDialog = false
    @State private var isProcessingMarkAll = false
    @State private var activeSheet: SettingsSheetDestination?
    @State private var isApprovingCLILink = false
    @State private var isDeletingAccount = false
    @State private var showingDeleteAccountConfirmation = false

    init(
        showsDismissButton: Bool = false
    ) {
        self.showsDismissButton = showsDismissButton
    }

    private var settings: AppSettings {
        dependencyFactory.appSettings
    }

    var body: some View {
        ScrollView {
            SettingsSectionStack(
                authState: authViewModel.authState,
                isApprovingCLILink: isApprovingCLILink,
                isDeletingAccount: isDeletingAccount,
                isFeedbackVisible: authViewModel.authState.authenticatedUser != nil,
                xConnection: xConnectionStore.connection,
                settings: settings,
                isProcessingMarkAll: isProcessingMarkAll,
                onLinkCLI: { activeSheet = .cliLinkScanner },
                onSignOut: authViewModel.logout,
                onDeleteAccount: { showingDeleteAccountConfirmation = true },
                onGiveFeedback: { activeSheet = .feedback },
                onMarkAll: { showMarkAllDialog = true },
                onOpenDebugMenu: openDebugMenu
            )
        }
        .font(.modalBody)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .appNavigationTitle(
            "Settings",
            accessibilityIdentifier: "settings.screen"
        )
        .toolbar {
            if showsDismissButton {
                ToolbarItem(placement: .navigationBarTrailing) {
                    // The system close role draws the glass xmark button.
                    Button(role: .close, action: dismiss.callAsFunction)
                        .accessibilityLabel("Close Settings")
                        .accessibilityIdentifier("settings.close")
                }
            }
        }
        .alert("Settings", isPresented: $showingAlert) {
            Button("OK", role: .cancel) { }
        } message: {
            Text(alertMessage)
        }
        .alert(
            "Mark all as read",
            isPresented: $showMarkAllDialog,
        ) {
            ForEach(MarkAllTarget.allCases, id: \.self) { target in
                Button(target.buttonTitle) {
                    Task { await markAllContent(for: target) }
                }
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("Choose which unread content should be marked as read.")
        }
        .confirmationDialog(
            "Permanently delete your account?",
            isPresented: $showingDeleteAccountConfirmation,
            titleVisibility: .visible
        ) {
            Button("Delete Account", role: .destructive) {
                Task { await deleteAccount() }
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("You will confirm with Apple. Newsbuddy will revoke connected services and permanently remove your account, content, conversations, and generated files.")
        }
        .sheet(item: $activeSheet) { destination in
            switch destination {
            #if DEBUG && targetEnvironment(simulator)
            case .debugMenu:
                DebugMenuView()
                    .environment(authViewModel)
            #endif
            case .cliLinkScanner:
                CLILinkScannerSheet { scannedCode in
                    Task {
                        await approveCLILink(scannedCode: scannedCode)
                    }
                }
            case .feedback:
                SettingsFeedbackSheet { message in
                    try await submitFeedback(message: message)
                }
            }
        }
        .task {
            await xConnectionStore.refresh()
        }
    }

    private var openDebugMenu: () -> Void {
        #if DEBUG && targetEnvironment(simulator)
        return { activeSheet = .debugMenu }
        #else
        return { }
        #endif
    }

    @MainActor
    private func deleteAccount() async {
        guard !isDeletingAccount else { return }
        isDeletingAccount = true
        defer { isDeletingAccount = false }
        do {
            try await dependencyFactory.authenticationService.deleteAccount()
            authViewModel.logout()
        } catch let authorizationError as ASAuthorizationError
            where authorizationError.code == .canceled {
            return
        } catch {
            alertMessage = "Account deletion failed: \(error.localizedDescription)"
            showingAlert = true
        }
    }

    @MainActor
    private func approveCLILink(scannedCode: String) async {
        guard !isApprovingCLILink else { return }

        isApprovingCLILink = true
        defer { isApprovingCLILink = false }

        do {
            let response = try await dependencyFactory.cliLinkService.approve(
                scannedCode: scannedCode,
                deviceName: UIDevice.current.name
            )
            activeSheet = nil
            alertMessage = "CLI linked with key prefix \(response.keyPrefix)."
            showingAlert = true
        } catch {
            alertMessage = error.localizedDescription
            showingAlert = true
        }
    }

    @MainActor
    private func submitFeedback(message: String) async throws {
        try await dependencyFactory.feedbackService.submit(message: message)
        alertMessage = "Thanks for the feedback."
        showingAlert = true
    }

    @MainActor
    private func markAllContent(for target: MarkAllTarget) async {
        guard !isProcessingMarkAll else { return }

        isProcessingMarkAll = true
        defer { isProcessingMarkAll = false }

        do {
            if let response = try await dependencyFactory.contentService.markAllAsRead(
                contentType: target.rawValue
            ) {
                if response.markedCount > 0 {
                    await badgeStatsStore.refreshStats()
                    alertMessage = "Marked \(response.markedCount) \(target.description(for: response.markedCount)) as read."
                } else {
                    alertMessage = "No unread \(target.description(for: 0)) found."
                }
            } else {
                alertMessage = "No unread \(target.description(for: 0)) found."
            }
        } catch {
            alertMessage = "Failed to mark as read: \(error.localizedDescription)"
        }

        showingAlert = true
    }
}
