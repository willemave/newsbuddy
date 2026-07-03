//
//  SettingsView.swift
//  newsly
//

import SwiftUI
import UIKit

struct SettingsView: View {
    @EnvironmentObject var authViewModel: AuthenticationViewModel
    @ObservedObject private var settings = AppSettings.shared
    private let scrollToCouncilOnAppear: Bool
    private let cliLinkService = CLILinkService()
    private let feedbackService = FeedbackService.shared
    @State private var showingAlert = false
    @State private var alertMessage = ""
    @State private var showMarkAllDialog = false
    @State private var isProcessingMarkAll = false
    @State private var showingFeedbackSheet = false
    @State private var showingDebugMenu = false
    @State private var showingCLILinkScanner = false
    @State private var isApprovingCLILink = false
    @State private var councilPersonasDraft: [CouncilPersona] = []
    @State private var serverCouncilPersonas: [CouncilPersona] = []
    @State private var newExpertName = ""
    @State private var hasUnsavedCouncilPersonaEdits = false
    @State private var isSavingCouncilPersonas = false
    @State private var xConnection: XConnectionResponse?

    init(scrollToCouncilOnAppear: Bool = false) {
        self.scrollToCouncilOnAppear = scrollToCouncilOnAppear
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(spacing: 24) {
                    brandHeader
                    accountSection
                    feedbackSection
                    twitterConfigurationSection
                    displayPreferencesSection
                    councilSection
                    sourcesSection
                    readStatusSection

                    #if DEBUG && targetEnvironment(simulator)
                    debugSection
                    #endif
                }
                .padding(.top, 8)
                .padding(.bottom, 120)
            }
            .onAppear {
                guard scrollToCouncilOnAppear else { return }
                DispatchQueue.main.async {
                    withAnimation(.easeInOut(duration: 0.2)) {
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
        .sheet(isPresented: $showingDebugMenu) {
            DebugMenuView()
                .environmentObject(authViewModel)
        }
        .sheet(isPresented: $showingCLILinkScanner) {
            CLILinkScannerSheet { scannedCode in
                Task {
                    await approveCLILink(scannedCode: scannedCode)
                }
            }
        }
        .sheet(isPresented: $showingFeedbackSheet) {
            FeedbackSheet { message in
                try await submitFeedback(message: message)
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

    // MARK: - Brand Header

    private var brandHeader: some View {
        VStack(spacing: 10) {
            Image("Mascot")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: 96, height: 96)
                .accessibilityLabel("Newsbuddy mascot")

            VStack(spacing: 2) {
                Text("Newsbuddy")
                    .font(.appTitle2.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                Text(appVersionLabel)
                    .font(.appFootnote)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 8)
        .padding(.bottom, 4)
    }

    private var appVersionLabel: String {
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "—"
        let build = info?["CFBundleVersion"] as? String ?? "—"
        return "Version \(version) (\(build))"
    }

    // MARK: - Account Section

    private var accountSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Account")

            if case .authenticated(let user) = authViewModel.authState {
                VStack(spacing: 0) {
                    AccountCard(user: user)

                    RowDivider(leadingInset: Spacing.rowHorizontal)

                    Button {
                        showingCLILinkScanner = true
                    } label: {
                        SettingsRow(
                            icon: "qrcode.viewfinder",
                            iconColor: .brandPrimary,
                            title: "Link CLI"
                        ) {
                            if isApprovingCLILink {
                                ProgressView()
                            } else {
                                NavigationChevron()
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(isApprovingCLILink)

                    RowDivider(leadingInset: Spacing.rowHorizontal)

                    Button {
                        authViewModel.logout()
                    } label: {
                        SettingsRow(
                            icon: "rectangle.portrait.and.arrow.right",
                            iconColor: .statusDestructive,
                            title: "Sign Out"
                        ) {
                            EmptyView()
                        }
                    }
                    .buttonStyle(.plain)
                }
                .settingsCard()
            }
        }
    }

    @ViewBuilder
    private var feedbackSection: some View {
        if authenticatedUser != nil {
            Button {
                showingFeedbackSheet = true
            } label: {
                SettingsRow(
                    icon: "bubble.left.and.bubble.right",
                    iconColor: .brandPrimary,
                    title: "Give Feedback"
                ) {
                    NavigationChevron()
                }
            }
            .buttonStyle(.plain)
            .settingsCard()
        }
    }

    // MARK: - X / Twitter Section

    private var twitterConfigurationSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "X / Twitter")

            if case .authenticated = authViewModel.authState {
                NavigationLink {
                    TwitterSettingsView()
                        .environmentObject(authViewModel)
                } label: {
                    SettingsRow(
                        icon: "at",
                        iconColor: .brandPrimary,
                        title: "X / Twitter",
                        subtitle: xConnection?.settingsSubtitle
                    )
                }
                .buttonStyle(.plain)
                .settingsCard()
            }
        }
    }

    // MARK: - Display Preferences Section

    private var displayPreferencesSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Display")

            VStack(spacing: 0) {
                readingExperienceRow

                RowDivider()

                textSizeRow
            }
            .settingsCard()
        }
    }

    private var councilSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Council")

            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 12) {
                    SettingsIcon(systemName: "person.3.sequence.fill", color: .brandPrimary)
                        .frame(width: 36, height: 36, alignment: .leading)

                    Text("Your Experts")
                        .font(.listTitle)
                        .foregroundStyle(Color.onSurface)

                    Spacer(minLength: 8)
                }

                // Expert list
                ForEach(Array(councilPersonasDraft.enumerated()), id: \.element.id) { index, persona in
                    HStack(alignment: .top, spacing: 12) {
                        Circle()
                            .fill(expertColor(for: index).opacity(0.15))
                            .frame(width: 36, height: 36)
                            .overlay(
                                Text(persona.displayName.prefix(1).uppercased())
                                    .font(.appSans(size: 15, weight: .semibold))
                                    .foregroundStyle(expertColor(for: index))
                            )
                            .accessibilityHidden(true)

                        Text(persona.displayName)
                            .font(.appBody)
                            .foregroundStyle(Color.onSurface)

                        Spacer()

                        Button {
                            removeExpert(at: index)
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.appSymbol(size: 20))
                                .foregroundStyle(Color.onSurfaceSecondary.opacity(0.5))
                                .frame(width: 44, height: 44)
                        }
                        .buttonStyle(.plain)
                        .contentShape(Circle())
                        .accessibilityLabel("Remove \(persona.displayName)")
                    }
                    .padding(.vertical, 8)
                    .background(Color.surfaceSecondary.opacity(0.55))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }

                // Add expert input
                if councilPersonasDraft.count < CouncilPersona.maxExperts {
                    HStack(spacing: 10) {
                        TextField("e.g. Paul Graham, Mariana Mazzucato", text: $newExpertName)
                            .textFieldStyle(.plain)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 10)
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                            .background(Color.surfaceTertiary, in: RoundedRectangle(cornerRadius: 12))
                            .submitLabel(.done)
                            .accessibilityLabel("Expert name")
                            .onSubmit { addExpert() }

                        Button {
                            addExpert()
                        } label: {
                            Image(systemName: "plus.circle.fill")
                                .font(.appSymbol(size: 24))
                                .foregroundStyle(Color.brandPrimary)
                                .frame(width: 44, height: 44)
                        }
                        .buttonStyle(.plain)
                        .contentShape(Circle())
                        .accessibilityLabel("Add expert")
                        .disabled(newExpertName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }

                HStack {
                    if councilPersonasDraft.count < CouncilPersona.minExperts {
                        Text("Add at least \(CouncilPersona.minExperts) experts to enable council chat.")
                            .font(.appCaption)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    } else {
                        Text("Tap the council button in chat to hear from your experts.")
                            .font(.appCaption)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                    Spacer()
                    Button {
                        Task { await saveCouncilPersonas() }
                    } label: {
                        Group {
                            if isSavingCouncilPersonas {
                                ProgressView()
                                    .controlSize(.small)
                                    .tint(.white)
                            } else {
                                Text("Save")
                                    .font(.appCallout.weight(.semibold))
                            }
                        }
                        .foregroundStyle(.white)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .frame(minHeight: 44)
                        .background(Color.terracottaPrimary, in: RoundedRectangle(cornerRadius: 10))
                    }
                    .buttonStyle(.plain)
                    .contentShape(Rectangle())
                    .accessibilityLabel("Save experts")
                    .disabled(isSavingCouncilPersonas || !hasUnsavedCouncilPersonaEdits)
                    .opacity((isSavingCouncilPersonas || !hasUnsavedCouncilPersonaEdits) ? 0.4 : 1.0)
                }
            }
            .padding(.horizontal, Spacing.rowHorizontal)
            .padding(.vertical, Spacing.rowVertical)
            .settingsCard()
        }
        .id("settings.council")
        .accessibilityIdentifier("settings.council_section")
    }

    private func expertColor(for _: Int) -> Color {
        // Single accent across all council expert avatars.
        .brandPrimary
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
        // Re-index sort orders
        councilPersonasDraft = councilPersonasDraft.enumerated().map { i, persona in
            CouncilPersona(id: persona.id, displayName: persona.displayName, sortOrder: i)
        }
        hasUnsavedCouncilPersonaEdits = councilPersonasDraft != serverCouncilPersonas
    }

    private var textSizeRow: some View {
        VStack(spacing: 0) {
            textSizeSlider(
                icon: "textformat.size",
                iconColor: .brandPrimary,
                title: "App Text Size",
                value: Binding(
                    get: { Double(settings.appTextSizeIndex) },
                    set: { settings.setAppTextSize(Int($0.rounded())) }
                ),
                range: 0...3
            )

            RowDivider()

            textSizeSlider(
                icon: "book",
                iconColor: .brandPrimary,
                title: "Content Text Size",
                value: Binding(
                    get: { Double(settings.contentTextSizeIndex) },
                    set: { settings.setContentTextSize(Int($0.rounded())) }
                ),
                range: 0...4
            )
        }
    }

    private var readingExperienceRow: some View {
        SettingsRow(
            icon: "newspaper",
            iconColor: .brandPrimary,
            title: "Reading Experience"
        ) {
            Picker(
                "Reading Experience",
                selection: Binding(
                    get: { settings.readingExperience },
                    set: { settings.setReadingExperience($0) }
                )
            ) {
                ForEach(ReadingExperience.allCases) { experience in
                    Text(experience.title).tag(experience)
                }
            }
            .pickerStyle(.segmented)
            .frame(width: 190)
            .accessibilityIdentifier("settings.reading_experience")
        }
    }

    private func textSizeSlider(
        icon: String,
        iconColor: Color,
        title: String,
        value: Binding<Double>,
        range: ClosedRange<Double>
    ) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                SettingsIcon(systemName: icon, color: iconColor)

                Text(title)
                    .font(.listTitle)
                    .foregroundStyle(Color.onSurface)

                Spacer(minLength: 8)
            }
            .padding(.horizontal, Spacing.rowHorizontal)
            .padding(.top, Spacing.rowVertical)

            HStack(spacing: 8) {
                Text("A")
                    .font(.appSans(size: 13, weight: .medium))
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .accessibilityHidden(true)

                Slider(value: value, in: range, step: 1)
                    .tint(Color.brandPrimary)
                    .frame(minHeight: 44)
                    .accessibilityLabel(title)
                    .accessibilityValue(textSizeAccessibilityValue(value.wrappedValue, range: range))

                Text("A")
                    .font(.appSans(size: 22, weight: .medium))
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .accessibilityHidden(true)
            }
            .padding(.leading, Spacing.rowDividerInset)
            .padding(.trailing, Spacing.rowHorizontal)
            .padding(.bottom, Spacing.rowVertical)
        }
    }

    private func textSizeAccessibilityValue(_ value: Double, range: ClosedRange<Double>) -> String {
        let stepCount = Int(range.upperBound - range.lowerBound) + 1
        let clampedValue = min(max(value, range.lowerBound), range.upperBound)
        let currentStep = Int(clampedValue - range.lowerBound) + 1
        return "\(currentStep) of \(stepCount)"
    }

    // MARK: - Sources Section

    private var sourcesSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Sources")

            VStack(spacing: 0) {
                NavigationLink {
                    FeedSourcesView()
                } label: {
                    SettingsRow(
                        icon: "list.bullet.rectangle",
                        iconColor: .brandPrimary,
                        title: "Feed Sources"
                    )
                }
                .buttonStyle(.plain)

                RowDivider(leadingInset: Spacing.rowHorizontal)

                NavigationLink {
                    PodcastSourcesView()
                } label: {
                    SettingsRow(
                        icon: "waveform",
                        iconColor: .brandPrimary,
                        title: "Podcast Sources"
                    )
                }
                .buttonStyle(.plain)
            }
            .settingsCard()
        }
    }

    // MARK: - Read Status Section

    private var readStatusSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Actions")

            Button {
                showMarkAllDialog = true
            } label: {
                SettingsRow(
                    icon: "checkmark.circle",
                    iconColor: .brandPrimary,
                    title: "Mark All As Read"
                ) {
                    if isProcessingMarkAll {
                        ProgressView()
                    } else {
                        EmptyView()
                    }
                }
            }
            .buttonStyle(.plain)
            .disabled(isProcessingMarkAll)
            .settingsCard()
        }
    }

    // MARK: - Debug Section

    private var debugSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Debug")

            Button {
                showingDebugMenu = true
            } label: {
                SettingsRow(
                    icon: "ladybug",
                    iconColor: .statusDestructive,
                    title: "Debug Menu"
                ) {
                    EmptyView()
                }
            }
            .buttonStyle(.plain)
            .settingsCard()
        }
    }

    private var authenticatedUser: User? {
        guard case .authenticated(let user) = authViewModel.authState else {
            return nil
        }
        return user
    }

    // MARK: - Actions

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
            showingCLILinkScanner = false
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
        let resolved = authenticatedUser?.councilPersonas ?? []
        serverCouncilPersonas = resolved
        if force || !hasUnsavedCouncilPersonaEdits {
            councilPersonasDraft = resolved
        }
        hasUnsavedCouncilPersonaEdits = councilPersonasDraft != serverCouncilPersonas
    }

    private func normalizedCouncilPersonas() -> [CouncilPersona] {
        councilPersonasDraft.enumerated().map { index, persona in
            CouncilPersona(
                id: persona.id,
                displayName: persona.displayName.trimmingCharacters(in: .whitespacesAndNewlines),
                sortOrder: index
            )
        }
    }

    @MainActor
    private func saveCouncilPersonas() async {
        guard !isSavingCouncilPersonas, authenticatedUser != nil else { return }

        let normalized = normalizedCouncilPersonas()
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

private struct FeedbackSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var message = ""
    @State private var isSubmitting = false
    @State private var errorMessage: String?

    let onSubmit: (String) async throws -> Void

    private var trimmedMessage: String {
        message.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                ZStack(alignment: .topLeading) {
                    TextEditor(text: $message)
                        .scrollContentBackground(.hidden)
                        .font(.appBody)
                        .foregroundStyle(Color.onSurface)
                        .frame(minHeight: 180)
                        .accessibilityLabel("Feedback message")
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        .background(Color.surfaceTertiary, in: RoundedRectangle(cornerRadius: 12))

                    if message.isEmpty {
                        Text("What should we improve?")
                            .font(.appBody)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .padding(.horizontal, 17)
                            .padding(.vertical, 18)
                            .allowsHitTesting(false)
                    }
                }

                if let errorMessage {
                    Text(errorMessage)
                        .font(.appFootnote)
                        .foregroundStyle(Color.statusDestructive)
                }

                Spacer(minLength: 0)
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.top, 20)
            .background(Color.surfacePrimary.ignoresSafeArea())
            .navigationTitle("Give Feedback")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                    .disabled(isSubmitting)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task { await submit() }
                    } label: {
                        if isSubmitting {
                            ProgressView()
                        } else {
                            Text("Submit")
                        }
                    }
                    .disabled(isSubmitting || trimmedMessage.isEmpty)
                }
            }
        }
    }

    @MainActor
    private func submit() async {
        let feedbackMessage = trimmedMessage
        guard !isSubmitting, !feedbackMessage.isEmpty else { return }
        isSubmitting = true
        errorMessage = nil
        do {
            try await onSubmit(feedbackMessage)
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
        isSubmitting = false
    }
}

// MARK: - Account Card

private struct AccountCard: View {
    let user: User

    var body: some View {
        HStack(spacing: 12) {
            Text(user.email.prefix(1).uppercased())
                .font(.appSans(size: 14, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: Spacing.iconSize, height: Spacing.iconSize)
                .background(Color.terracottaPrimary, in: Circle())
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text(user.fullName ?? user.email)
                    .font(.listTitle.weight(.medium))
                    .foregroundStyle(Color.onSurface)

                if user.fullName != nil {
                    Text(user.email)
                        .font(.listCaption)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
            }

            Spacer()
        }
        .padding(.vertical, Spacing.rowVertical)
        .padding(.horizontal, Spacing.rowHorizontal)
    }
}

// MARK: - Mark All Target

private enum MarkAllTarget: String, CaseIterable {
    case article
    case podcast
    case news

    var singularLabel: String {
        switch self {
        case .article: return "Article"
        case .podcast: return "Podcast"
        case .news: return "News item"
        }
    }

    var pluralLabel: String {
        switch self {
        case .article: return "Articles"
        case .podcast: return "Podcasts"
        case .news: return "News items"
        }
    }

    var buttonTitle: String {
        "Mark all \(pluralLabel.lowercased()) as read"
    }

    func description(for count: Int) -> String {
        count == 1 ? singularLabel.lowercased() : pluralLabel.lowercased()
    }
}
