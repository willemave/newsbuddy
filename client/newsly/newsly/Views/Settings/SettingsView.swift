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
    @State private var showingAlert = false
    @State private var alertMessage = ""
    @State private var showMarkAllDialog = false
    @State private var isProcessingMarkAll = false
    @State private var showingDebugMenu = false
    @State private var showingCLILinkScanner = false
    @State private var isApprovingCLILink = false
    @State private var newsListPreferencePromptDraft = ""
    @State private var serverNewsListPreferencePrompt = ""
    @State private var hasUnsavedNewsListPreferencePromptEdits = false
    @State private var isSavingNewsListPreferencePrompt = false
    @State private var councilPersonasDraft: [CouncilPersona] = []
    @State private var serverCouncilPersonas: [CouncilPersona] = []
    @State private var newExpertName = ""
    @State private var hasUnsavedCouncilPersonaEdits = false
    @State private var isSavingCouncilPersonas = false
    @State private var xConnection: XConnectionResponse?
    @FocusState private var isNewsListPreferencePromptFocused: Bool

    init(scrollToCouncilOnAppear: Bool = false) {
        self.scrollToCouncilOnAppear = scrollToCouncilOnAppear
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(spacing: 24) {
                    brandHeader
                    accountSection
                    twitterConfigurationSection
                    displayPreferencesSection
                    councilSection
                    newsListPreferencesSection
                    sourcesSection
                    readStatusSection

                    #if DEBUG && targetEnvironment(simulator)
                    debugSection
                    #endif
                }
                .padding(.top, 8)
                .padding(.bottom, 40)
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
        .confirmationDialog(
            "Mark all as read",
            isPresented: $showMarkAllDialog,
            titleVisibility: .visible
        ) {
            ForEach(MarkAllTarget.allCases, id: \.self) { target in
                Button(target.buttonTitle) {
                    Task { await markAllContent(for: target) }
                }
            }
            Button("Cancel", role: .cancel) { }
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
        .onChange(of: authViewModel.authState) { _, _ in
            syncNewsListPreferencePromptWithAuthenticatedUser(force: true)
            syncCouncilPersonasWithAuthenticatedUser(force: true)
            Task { await loadXConnectionState(force: true) }
        }
        .task {
            syncNewsListPreferencePromptWithAuthenticatedUser(force: true)
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
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                Text(appVersionLabel)
                    .font(.footnote)
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
                            iconColor: .green,
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
                        iconColor: .blue,
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
                textSizeRow

                RowDivider(leadingInset: Spacing.rowHorizontal)

                longArticleDisplayModeRow
            }
            .settingsCard()
        }
    }

    private var newsListPreferencesSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "News List")

            if authenticatedUser != nil {
                VStack(spacing: 0) {
                    newsListPreferencePromptRow
                }
                .settingsCard()
            }
        }
    }

    private var councilSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Council")

            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    SettingsIcon(systemName: "person.3.sequence.fill", color: .orange)

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
                                    .font(.system(size: 15, weight: .semibold, design: .rounded))
                                    .foregroundStyle(expertColor(for: index))
                            )

                        Text(persona.displayName)
                            .font(.body)
                            .foregroundStyle(Color.onSurface)

                        Spacer()

                        Button {
                            removeExpert(at: index)
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.system(size: 20))
                                .foregroundStyle(Color.onSurfaceSecondary.opacity(0.5))
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
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
                            .background(Color.surfaceTertiary, in: RoundedRectangle(cornerRadius: 12))
                            .submitLabel(.done)
                            .onSubmit { addExpert() }

                        Button {
                            addExpert()
                        } label: {
                            Image(systemName: "plus.circle.fill")
                                .font(.system(size: 24))
                                .foregroundStyle(.orange)
                        }
                        .buttonStyle(.plain)
                        .disabled(newExpertName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }

                HStack {
                    if councilPersonasDraft.count < CouncilPersona.minExperts {
                        Text("Add at least \(CouncilPersona.minExperts) experts to enable council chat.")
                            .font(.caption)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    } else {
                        Text("Tap the council button in chat to hear from your experts.")
                            .font(.caption)
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
                                    .font(.callout.weight(.semibold))
                            }
                        }
                        .foregroundStyle(.white)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .background(Color.terracottaPrimary, in: RoundedRectangle(cornerRadius: 10))
                    }
                    .buttonStyle(.plain)
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

    private func expertColor(for index: Int) -> Color {
        let colors: [Color] = [.orange, .blue, .purple]
        return colors[index % colors.count]
    }

    private func addExpert() {
        let name = newExpertName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, councilPersonasDraft.count < CouncilPersona.maxExperts else { return }
        let persona = CouncilPersona(name: name, sortOrder: councilPersonasDraft.count)
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

    private var newsListPreferencePromptRow: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                SettingsIcon(systemName: "text.badge.star", color: .orange)

                Text("News List Preferences")
                    .font(.listTitle)
                    .foregroundStyle(Color.onSurface)

                Spacer(minLength: 8)
            }

            TextEditor(text: $newsListPreferencePromptDraft)
                .focused($isNewsListPreferencePromptFocused)
                .scrollContentBackground(.hidden)
                .font(.body)
                .foregroundStyle(Color.onSurface)
                .frame(minHeight: 140)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Color.surfaceTertiary, in: RoundedRectangle(cornerRadius: 12))
                .onChange(of: newsListPreferencePromptDraft) { _, newValue in
                    hasUnsavedNewsListPreferencePromptEdits =
                        normalizedNewsListPreferencePromptForComparison(newValue)
                        != normalizedNewsListPreferencePromptForComparison(serverNewsListPreferencePrompt)
                }

            HStack(spacing: 12) {
                Spacer()

                Button {
                    newsListPreferencePromptDraft = ""
                    hasUnsavedNewsListPreferencePromptEdits =
                        normalizedNewsListPreferencePromptForComparison("")
                        != normalizedNewsListPreferencePromptForComparison(serverNewsListPreferencePrompt)
                } label: {
                    Text("Reset")
                        .font(.callout.weight(.semibold))
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .background(Color.surfaceTertiary, in: RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)

                Button {
                    Task { await saveNewsListPreferencePrompt() }
                } label: {
                    Group {
                        if isSavingNewsListPreferencePrompt {
                            ProgressView()
                                .controlSize(.small)
                                .tint(.white)
                        } else {
                            Text("Save")
                                .font(.callout.weight(.semibold))
                        }
                    }
                    .foregroundStyle(.white)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                    .background(Color.terracottaPrimary, in: RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
                .disabled(
                    isSavingNewsListPreferencePrompt || !hasUnsavedNewsListPreferencePromptEdits
                )
                .opacity((isSavingNewsListPreferencePrompt || !hasUnsavedNewsListPreferencePromptEdits) ? 0.4 : 1.0)
            }
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, Spacing.rowVertical)
    }

    private var textSizeRow: some View {
        VStack(spacing: 0) {
            textSizeSlider(
                icon: "textformat.size",
                iconColor: .orange,
                title: "App Text Size",
                value: Binding(
                    get: { Double(settings.appTextSizeIndex) },
                    set: { settings.appTextSizeIndex = Int($0.rounded()) }
                ),
                range: 0...3
            )

            RowDivider()

            textSizeSlider(
                icon: "book",
                iconColor: .purple,
                title: "Content Text Size",
                value: Binding(
                    get: { Double(settings.contentTextSizeIndex) },
                    set: { settings.contentTextSizeIndex = Int($0.rounded()) }
                ),
                range: 0...4
            )
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
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Color.onSurfaceSecondary)

                Slider(value: value, in: range, step: 1)
                    .tint(.accentColor)

                Text("A")
                    .font(.system(size: 22, weight: .medium))
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .padding(.leading, Spacing.rowDividerInset)
            .padding(.trailing, Spacing.rowHorizontal)
            .padding(.bottom, Spacing.rowVertical)
        }
    }

    private var longArticleDisplayModeRow: some View {
        VStack(alignment: .leading, spacing: 10) {
            let selectedMode = LongArticleDisplayMode(rawValue: settings.longArticleDisplayMode) ?? .both

            HStack(spacing: 12) {
                SettingsIcon(systemName: "doc.text.magnifyingglass", color: .indigo)

                Text("Long Article Format")
                    .font(.listTitle)
                    .foregroundStyle(Color.onSurface)

                Spacer(minLength: 8)
            }

            Picker("Long Article Format", selection: $settings.longArticleDisplayMode) {
                ForEach(LongArticleDisplayMode.allCases, id: \.rawValue) { mode in
                    Text(mode.title).tag(mode.rawValue)
                }
            }
            .pickerStyle(.segmented)
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, Spacing.rowVertical)
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
                        iconColor: .blue,
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
                        iconColor: .purple,
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
                    iconColor: .green,
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
                    iconColor: .red,
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
    private func syncNewsListPreferencePromptWithAuthenticatedUser(force: Bool) {
        guard !isSavingNewsListPreferencePrompt else { return }
        guard let user = authenticatedUser else {
            serverNewsListPreferencePrompt = ""
            if force || !isNewsListPreferencePromptFocused {
                newsListPreferencePromptDraft = ""
            }
            hasUnsavedNewsListPreferencePromptEdits = false
            return
        }

        serverNewsListPreferencePrompt = user.newsListPreferencePrompt
        if force || (!isNewsListPreferencePromptFocused && !hasUnsavedNewsListPreferencePromptEdits)
        {
            newsListPreferencePromptDraft = user.newsListPreferencePrompt
        }
        hasUnsavedNewsListPreferencePromptEdits =
            normalizedNewsListPreferencePromptForComparison(newsListPreferencePromptDraft)
            != normalizedNewsListPreferencePromptForComparison(serverNewsListPreferencePrompt)
    }

    private func normalizedNewsListPreferencePromptDraft() -> String? {
        let trimmed = newsListPreferencePromptDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func normalizedNewsListPreferencePromptForComparison(_ value: String) -> String {
        value.trimmingCharacters(in: .whitespacesAndNewlines)
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
    private func saveNewsListPreferencePrompt() async {
        guard !isSavingNewsListPreferencePrompt, authenticatedUser != nil else { return }
        isSavingNewsListPreferencePrompt = true
        defer { isSavingNewsListPreferencePrompt = false }

        do {
            let user = try await AuthenticationService.shared.updateCurrentUserProfile(
                newsListPreferencePrompt: normalizedNewsListPreferencePromptDraft()
            )
            authViewModel.updateUser(user)
            serverNewsListPreferencePrompt = user.newsListPreferencePrompt
            newsListPreferencePromptDraft = user.newsListPreferencePrompt
            hasUnsavedNewsListPreferencePromptEdits = false
            alertMessage = "News list preferences saved."
            showingAlert = true
        } catch {
            alertMessage = "Failed to save news list preferences: \(error.localizedDescription)"
            showingAlert = true
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

// MARK: - Settings Card Modifier

private extension View {
    func settingsCard() -> some View {
        self
            .background(Color.surfaceSecondary)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .padding(.horizontal, Spacing.screenHorizontal)
    }
}

// MARK: - Account Card

private struct AccountCard: View {
    let user: User

    var body: some View {
        HStack(spacing: 12) {
            Text(user.email.prefix(1).uppercased())
                .font(.system(size: 14, weight: .semibold, design: .rounded))
                .foregroundStyle(.white)
                .frame(width: Spacing.iconSize, height: Spacing.iconSize)
                .background(Color.terracottaPrimary, in: Circle())

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

// MARK: - Navigation

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
