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
    @Environment(AuthenticationViewModel.self) private var authViewModel
    @Environment(BadgeStatsStore.self) private var badgeStatsStore
    @Environment(RootDependencyFactory.self) private var dependencyFactory
    private let scrollToCouncilOnAppear: Bool
    @State private var showingAlert = false
    @State private var alertMessage = ""
    @State private var showMarkAllDialog = false
    @State private var isProcessingMarkAll = false
    @State private var activeSheet: SettingsSheetDestination?
    @State private var isApprovingCLILink = false
    @State private var isDeletingAccount = false
    @State private var showingDeleteAccountConfirmation = false
    @State private var councilPersonasDraft: [CouncilPersona] = []
    @State private var serverCouncilPersonas: [CouncilPersona] = []
    @State private var newExpertName = ""
    @State private var hasUnsavedCouncilPersonaEdits = false
    @State private var isSavingCouncilPersonas = false
    @State private var xConnection: XConnectionResponse?

    init(scrollToCouncilOnAppear: Bool = false) {
        self.scrollToCouncilOnAppear = scrollToCouncilOnAppear
    }

    private var settings: AppSettings {
        dependencyFactory.appSettings
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                SettingsSectionStack(
                    authState: authViewModel.authState,
                    isApprovingCLILink: isApprovingCLILink,
                    isDeletingAccount: isDeletingAccount,
                    isFeedbackVisible: authViewModel.authState.authenticatedUser != nil,
                    xConnection: xConnection,
                    settings: settings,
                    councilPersonas: councilPersonasDraft,
                    newExpertName: $newExpertName,
                    isSavingCouncilPersonas: isSavingCouncilPersonas,
                    hasUnsavedCouncilPersonaEdits: hasUnsavedCouncilPersonaEdits,
                    isProcessingMarkAll: isProcessingMarkAll,
                    onLinkCLI: { activeSheet = .cliLinkScanner },
                    onSignOut: authViewModel.logout,
                    onDeleteAccount: { showingDeleteAccountConfirmation = true },
                    onGiveFeedback: { activeSheet = .feedback },
                    onAddExpert: addExpert,
                    onRemoveExpert: removeExpert,
                    onSaveCouncilPersonas: saveCouncilPersonasIncludingPending,
                    onMarkAll: { showMarkAllDialog = true },
                    onOpenDebugMenu: openDebugMenu
                )
            }
            .onAppear {
                guard scrollToCouncilOnAppear else { return }
                Task { @MainActor in
                    await Task.yield()
                    withAnimation(AppMotion.subtle) {
                        proxy.scrollTo("settings.council", anchor: .top)
                    }
                }
            }
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        .toolbarBackground(Color.surfacePrimary, for: .navigationBar)
        .appNavigationTitle(
            "Settings",
            accessibilityIdentifier: "settings.screen"
        )
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
        .onChange(of: authViewModel.authState) { _, _ in
            syncCouncilPersonasWithAuthenticatedUser(force: true)
            Task { await loadXConnectionState(force: true) }
        }
        .task {
            syncCouncilPersonasWithAuthenticatedUser(force: true)
            await loadXConnectionState(force: true)
        }
    }

    private var openDebugMenu: () -> Void {
        #if DEBUG && targetEnvironment(simulator)
        return { activeSheet = .debugMenu }
        #else
        return { }
        #endif
    }

    private func addExpert() {
        let name = newExpertName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, councilPersonasDraft.count < CouncilPersona.maxExperts else { return }
        let persona = CouncilPersona(name: name, sortOrder: councilPersonasDraft.count)
        guard !councilPersonasDraft.contains(where: { $0.id == persona.id }) else {
            alertMessage = "\(name) is already in your council."
            showingAlert = true
            return
        }
        councilPersonasDraft.append(persona)
        newExpertName = ""
        hasUnsavedCouncilPersonaEdits = councilPersonasDraft != serverCouncilPersonas
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

    private func removeExpert(at index: Int) {
        councilPersonasDraft.remove(at: index)
        councilPersonasDraft = councilPersonasDraft.enumerated().map { i, persona in
            CouncilPersona(id: persona.id, displayName: persona.displayName, sortOrder: i)
        }
        hasUnsavedCouncilPersonaEdits = councilPersonasDraft != serverCouncilPersonas
    }

    private func saveCouncilPersonasIncludingPending() {
        if !newExpertName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            addExpert()
            guard newExpertName.isEmpty else { return }
        }
        Task { await saveCouncilPersonas() }
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
    private func syncCouncilPersonasWithAuthenticatedUser(force: Bool) {
        guard !isSavingCouncilPersonas else { return }
        let resolved = authViewModel.authState.authenticatedUser?.councilPersonas ?? []
        serverCouncilPersonas = resolved
        if force || !hasUnsavedCouncilPersonaEdits {
            councilPersonasDraft = resolved
        }
        hasUnsavedCouncilPersonaEdits = councilPersonasDraft != serverCouncilPersonas
    }

    @MainActor
    private func saveCouncilPersonas() async {
        guard !isSavingCouncilPersonas, authViewModel.authState.authenticatedUser != nil else { return }

        let normalized = councilPersonasDraft.normalizedForSettings()
        guard normalized.count >= CouncilPersona.minExperts,
              normalized.count <= CouncilPersona.maxExperts,
              normalized.allSatisfy({ !$0.displayName.isEmpty }) else {
            alertMessage = "Add \(CouncilPersona.minExperts)-\(CouncilPersona.maxExperts) experts with names to save."
            showingAlert = true
            return
        }

        isSavingCouncilPersonas = true
        defer { isSavingCouncilPersonas = false }

        do {
            let user = try await dependencyFactory.authenticationService.updateCurrentUserProfile(
                councilPersonas: normalized
            )
            authViewModel.updateUser(user)
            serverCouncilPersonas = user.councilPersonas
            councilPersonasDraft = user.councilPersonas
            hasUnsavedCouncilPersonaEdits = false
            alertMessage = "Experts saved."
            showingAlert = true
        } catch {
            alertMessage = "Failed to save experts: \(error.localizedDescription)"
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

    @MainActor
    private func loadXConnectionState(force: Bool) async {
        guard case .authenticated = authViewModel.authState else {
            xConnection = nil
            return
        }
        if !force, xConnection != nil {
            return
        }
        do {
            xConnection = try await dependencyFactory.xIntegrationService.fetchConnection()
        } catch {
            xConnection = nil
        }
    }
}
