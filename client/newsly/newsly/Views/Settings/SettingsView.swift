//
//  SettingsView.swift
//  newsly
//

import SwiftUI
import UIKit

private enum SettingsSheetDestination: String, Identifiable {
    case debugMenu
    case cliLinkScanner
    case feedback

    var id: String { rawValue }
}

struct SettingsView: View {
    @Environment(AuthenticationViewModel.self) private var authViewModel
    private let scrollToCouncilOnAppear: Bool
    private let cliLinkService = CLILinkService()
    private let feedbackService = FeedbackService.shared
    @State private var settings = AppSettings.shared
    @State private var showingAlert = false
    @State private var alertMessage = ""
    @State private var showMarkAllDialog = false
    @State private var isProcessingMarkAll = false
    @State private var activeSheet: SettingsSheetDestination?
    @State private var isApprovingCLILink = false
    @State private var councilPersonasDraft: [CouncilPersona] = []
    @State private var serverCouncilPersonas: [CouncilPersona] = []
    @State private var newExpertName = ""
    @State private var hasUnsavedCouncilPersonaEdits = false
    @State private var isSavingCouncilPersonas = false
    @State private var isSavingReadingExperience = false
    @State private var xConnection: XConnectionResponse?

    init(scrollToCouncilOnAppear: Bool = false) {
        self.scrollToCouncilOnAppear = scrollToCouncilOnAppear
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                SettingsSectionStack(
                    authState: authViewModel.authState,
                    isApprovingCLILink: isApprovingCLILink,
                    isFeedbackVisible: authViewModel.authState.authenticatedUser != nil,
                    xConnection: xConnection,
                    settings: settings,
                    isSavingReadingExperience: isSavingReadingExperience,
                    councilPersonas: councilPersonasDraft,
                    newExpertName: $newExpertName,
                    isSavingCouncilPersonas: isSavingCouncilPersonas,
                    hasUnsavedCouncilPersonaEdits: hasUnsavedCouncilPersonaEdits,
                    isProcessingMarkAll: isProcessingMarkAll,
                    onLinkCLI: { activeSheet = .cliLinkScanner },
                    onSignOut: authViewModel.logout,
                    onGiveFeedback: { activeSheet = .feedback },
                    onAddExpert: addExpert,
                    onRemoveExpert: removeExpert,
                    onSaveCouncilPersonas: { Task { await saveCouncilPersonas() } },
                    onMarkAll: { showMarkAllDialog = true },
                    onOpenDebugMenu: { activeSheet = .debugMenu },
                    onReadingExperienceChanged: updateReadingExperience
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
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
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
        .sheet(item: $activeSheet) { destination in
            switch destination {
            case .debugMenu:
                DebugMenuView()
                    .environment(authViewModel)
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

    private func removeExpert(at index: Int) {
        councilPersonasDraft.remove(at: index)
        councilPersonasDraft = councilPersonasDraft.enumerated().map { i, persona in
            CouncilPersona(id: persona.id, displayName: persona.displayName, sortOrder: i)
        }
        hasUnsavedCouncilPersonaEdits = councilPersonasDraft != serverCouncilPersonas
    }

    @MainActor
    private func approveCLILink(scannedCode: String) async {
        guard !isApprovingCLILink else { return }

        isApprovingCLILink = true
        defer { isApprovingCLILink = false }

        do {
            let response = try await cliLinkService.approve(
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
            let user = try await AuthenticationService.shared.updateCurrentUserProfile(
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
    private func updateReadingExperience(_ experience: ReadingExperience) {
        guard !isSavingReadingExperience, settings.readingExperience != experience else { return }
        let previous = settings.readingExperience
        settings.setReadingExperience(experience)
        isSavingReadingExperience = true
        Task {
            defer { isSavingReadingExperience = false }
            do {
                let user = try await AuthenticationService.shared.updateCurrentUserProfile(
                    readingExperience: experience
                )
                authViewModel.updateUser(user)
            } catch {
                settings.setReadingExperience(previous)
                alertMessage = "Failed to save reading experience: \(error.localizedDescription)"
                showingAlert = true
            }
        }
    }

    @MainActor
    private func submitFeedback(message: String) async throws {
        try await feedbackService.submit(message: message)
        alertMessage = "Thanks for the feedback."
        showingAlert = true
    }

    @MainActor
    private func markAllContent(for target: MarkAllTarget) async {
        guard !isProcessingMarkAll else { return }

        isProcessingMarkAll = true
        defer { isProcessingMarkAll = false }

        do {
            if let response = try await ContentService.shared.markAllAsRead(contentType: target.rawValue) {
                if response.markedCount > 0 {
                    await UnreadCountService.shared.refreshCounts()
                    alertMessage = "Marked \(response.markedCount) \(target.description(for: response.markedCount)) as read."
                } else {
                    alertMessage = "No unread \(target.description(for: 0)) found."
                }
            } else {
                alertMessage = "No unread \(target.description(for: 0)) found."
            }
        } catch let apiError as APIError {
            alertMessage = "Failed to mark as read: \(apiError.localizedDescription)"
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
            xConnection = try await XIntegrationService.shared.fetchConnection()
        } catch {
            xConnection = nil
        }
    }
}
