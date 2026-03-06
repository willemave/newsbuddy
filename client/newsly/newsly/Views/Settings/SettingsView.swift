//
//  SettingsView.swift
//  newsly
//

import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var authViewModel: AuthenticationViewModel
    @ObservedObject private var settings = AppSettings.shared
    @State private var showingAlert = false
    @State private var alertMessage = ""
    @State private var showMarkAllDialog = false
    @State private var isProcessingMarkAll = false
    @State private var showingDebugMenu = false
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
                accountSection
                SectionDivider()

                xIntegrationSection
                SectionDivider()

                displayPreferencesSection
                SectionDivider()

                sourcesSection
                SectionDivider()

                readStatusSection

                #if DEBUG && targetEnvironment(simulator)
                SectionDivider()
                debugSection
                #endif

                Spacer(minLength: 40)
            }
        }
        .background(Color.surfacePrimary)
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
        .task {
            await loadAccountState(force: true)
        }
        .onChange(of: authViewModel.authState) { _, _ in
            Task { await loadAccountState(force: true) }
        }
    }

    // MARK: - Account Section

    private var accountSection: some View {
        VStack(spacing: 0) {
            SectionHeader(title: "Account")

            if case .authenticated(let user) = authViewModel.authState {
                AccountCard(user: user)

                RowDivider()

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
        }
    }

    // MARK: - X Integration Section

    private var xIntegrationSection: some View {
        VStack(spacing: 0) {
            SectionHeader(title: "X Integration")

            if case .authenticated = authViewModel.authState {
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
                .disabled(isSavingTwitterUsername || isUpdatingXConnection)

                RowDivider()

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
                            title: "Connect X",
                            subtitle: "Authorize bookmark sync from your X account"
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
    }

    private var usernameInputRow: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Username")
                .font(.listCaption)
                .foregroundStyle(Color.textTertiary)
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

    // MARK: - Display Preferences Section

    private var displayPreferencesSection: some View {
        VStack(spacing: 0) {
            SectionHeader(title: "Display")

            textSizeRow

            RowDivider()

            fastNewsModeRow
        }
    }

    private var textSizeRow: some View {
        VStack(spacing: 0) {
            textSizeSlider(
                icon: "textformat.size",
                iconColor: .orange,
                title: "App Text Size",
                subtitle: AppTextSize(index: settings.appTextSizeIndex).label,
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
                subtitle: ContentTextSize(index: settings.contentTextSizeIndex).label,
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
        subtitle: String,
        value: Binding<Double>,
        range: ClosedRange<Double>
    ) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(iconColor)
                    .frame(width: Spacing.iconSize, height: Spacing.iconSize)

                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.listTitle)
                        .foregroundStyle(Color.textPrimary)

                    Text(subtitle)
                        .font(.listCaption)
                        .foregroundStyle(Color.textTertiary)
                }

                Spacer(minLength: 8)
            }
            .padding(.horizontal, Spacing.rowHorizontal)
            .padding(.top, Spacing.rowVertical)

            HStack(spacing: 12) {
                Text("A")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Color.textTertiary)

                Slider(value: value, in: range, step: 1)
                    .tint(.accentColor)

                Text("A")
                    .font(.system(size: 22, weight: .medium))
                    .foregroundStyle(Color.textTertiary)
            }
            .padding(.horizontal, Spacing.rowHorizontal)
            .padding(.bottom, Spacing.rowVertical)
        }
    }

    private var fastNewsModeRow: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Image(systemName: "bolt.fill")
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(.teal)
                    .frame(width: Spacing.iconSize, height: Spacing.iconSize)

                VStack(alignment: .leading, spacing: 2) {
                    Text("Fast News View")
                        .font(.listTitle)
                        .foregroundStyle(Color.textPrimary)
                    Text("Choose between article stream and daily roll-up cards")
                        .font(.listCaption)
                        .foregroundStyle(Color.textTertiary)
                }
                Spacer(minLength: 8)
            }

            Picker("Fast News View", selection: $settings.fastNewsMode) {
                ForEach(FastNewsMode.allCases, id: \.rawValue) { mode in
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
        VStack(spacing: 0) {
            SectionHeader(title: "Sources")

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

            RowDivider()

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
    }

    // MARK: - Read Status Section

    private var readStatusSection: some View {
        VStack(spacing: 0) {
            SectionHeader(title: "Actions")

            Button {
                showMarkAllDialog = true
            } label: {
                SettingsRow(
                    icon: "checkmark.circle",
                    iconColor: .green,
                    title: "Mark All As Read",
                    subtitle: "Choose content type to mark as read"
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
        }
    }

    // MARK: - Debug Section

    private var debugSection: some View {
        VStack(spacing: 0) {
            SectionHeader(title: "Debug")

            SettingsToggleRow(
                icon: "text.bubble",
                iconColor: .orange,
                title: "Show Live Voice Transcript",
                subtitle: "Display transcript/assistant text on Live tab",
                isOn: $settings.showLiveVoiceDebugText
            )

            RowDivider()

            Button {
                showingDebugMenu = true
            } label: {
                SettingsRow(
                    icon: "ladybug",
                    iconColor: .red,
                    title: "Debug Menu",
                    subtitle: "Test authentication (Simulator)"
                ) {
                    EmptyView()
                }
            }
            .buttonStyle(.plain)
        }
    }

    private var isXConnected: Bool {
        xConnection?.connected == true
    }

    private var xConnectionSubtitle: String {
        if let username = xConnection?.providerUsername, !username.isEmpty {
            return "@\(username)"
        }
        if let lastStatus = xConnection?.lastStatus, !lastStatus.isEmpty {
            return "Status: \(lastStatus)"
        }
        return "Connected"
    }

    private var authenticatedUser: User? {
        guard case .authenticated(let user) = authViewModel.authState else {
            return nil
        }
        return user
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
            // Prefer the authenticated profile username; only fall back to integration username when empty.
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

    // MARK: - Actions

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
}

// MARK: - Account Card

private struct AccountCard: View {
    let user: User

    var body: some View {
        HStack(spacing: 12) {
            // Avatar
            Circle()
                .fill(Color.accentColor.opacity(0.15))
                .frame(width: 44, height: 44)
                .overlay(
                    Text(user.email.prefix(1).uppercased())
                        .font(.headline)
                        .foregroundStyle(.tint)
                )

            VStack(alignment: .leading, spacing: 2) {
                Text(user.fullName ?? user.email)
                    .font(.listTitle)
                    .foregroundStyle(Color.textPrimary)

                if user.fullName != nil {
                    Text(user.email)
                        .font(.listCaption)
                        .foregroundStyle(Color.textTertiary)
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
