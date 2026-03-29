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
    @State private var selectedDigestIntervalHours = NewsDigestIntervalOption.every6Hours.rawValue
    @State private var isSavingDigestInterval = false
    @State private var digestPreferencePromptDraft = ""
    @State private var serverDigestPreferencePrompt = ""
    @State private var hasUnsavedDigestPreferencePromptEdits = false
    @State private var isSavingDigestPreferencePrompt = false
    @FocusState private var isDigestPreferencePromptFocused: Bool

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                accountSection
                SectionDivider()

                twitterConfigurationSection
                SectionDivider()

                displayPreferencesSection
                SectionDivider()

                digestPreferencesSection
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
        .onChange(of: authViewModel.authState) { _, _ in
            syncDigestIntervalWithAuthenticatedUser()
            syncDigestPreferencePromptWithAuthenticatedUser(force: true)
        }
        .task {
            syncDigestIntervalWithAuthenticatedUser()
            syncDigestPreferencePromptWithAuthenticatedUser(force: true)
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

    // MARK: - X / Twitter Section

    private var twitterConfigurationSection: some View {
        VStack(spacing: 0) {
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
                        subtitle: "Username and account connection"
                    )
                }
                .buttonStyle(.plain)
            }
        }
    }

    // MARK: - Display Preferences Section

    private var displayPreferencesSection: some View {
        VStack(spacing: 0) {
            SectionHeader(title: "Display")

            textSizeRow

            RowDivider()

            longArticleDisplayModeRow

            RowDivider()

            fastNewsModeRow
        }
    }

    private var digestPreferencesSection: some View {
        VStack(spacing: 0) {
            SectionHeader(title: "Daily Digest")

            if let user = authenticatedUser {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 12) {
                        Image(systemName: "clock.badge")
                            .font(.system(size: 17, weight: .medium))
                            .foregroundStyle(.indigo)
                            .frame(width: Spacing.iconSize, height: Spacing.iconSize)

                        VStack(alignment: .leading, spacing: 2) {
                            Text("Digest Frequency")
                                .font(.listTitle)
                                .foregroundStyle(Color.textPrimary)
                            Text(
                                isSavingDigestInterval
                                    ? "Saving..."
                                    : NewsDigestIntervalOption(rawValue: user.newsDigestIntervalHours)?.detail
                                        ?? "Every 6 hours"
                            )
                            .font(.listCaption)
                            .foregroundStyle(Color.textTertiary)
                        }
                        Spacer(minLength: 8)
                    }

                    Picker("Digest Frequency", selection: $selectedDigestIntervalHours) {
                        ForEach(NewsDigestIntervalOption.allCases, id: \.rawValue) { option in
                            Text(option.title).tag(option.rawValue)
                        }
                    }
                    .pickerStyle(.segmented)
                    .disabled(isSavingDigestInterval)
                    .onChange(of: selectedDigestIntervalHours) { _, newValue in
                        guard newValue != user.newsDigestIntervalHours else { return }
                        Task { await saveDigestIntervalHours(newValue) }
                    }
                }
                .padding(.horizontal, Spacing.rowHorizontal)
                .padding(.vertical, Spacing.rowVertical)

                RowDivider()

                digestPreferencePromptRow
            }
        }
    }

    private var digestPreferencePromptRow: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Image(systemName: "text.badge.star")
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(.orange)
                    .frame(width: Spacing.iconSize, height: Spacing.iconSize)

                VStack(alignment: .leading, spacing: 2) {
                    Text("Digest Preferences")
                        .font(.listTitle)
                        .foregroundStyle(Color.textPrimary)
                    Text("Used to curate digest bullets and filter X posts before they enter the digest.")
                        .font(.listCaption)
                        .foregroundStyle(Color.textTertiary)
                }

                Spacer(minLength: 8)
            }

            TextEditor(text: $digestPreferencePromptDraft)
                .focused($isDigestPreferencePromptFocused)
                .frame(minHeight: 140)
                .padding(.horizontal, 8)
                .padding(.vertical, 8)
                .background(Color.surfaceSecondary)
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .onChange(of: digestPreferencePromptDraft) { _, newValue in
                    hasUnsavedDigestPreferencePromptEdits =
                        normalizedDigestPreferencePromptForComparison(newValue)
                        != normalizedDigestPreferencePromptForComparison(serverDigestPreferencePrompt)
                }

            HStack {
                Text("Clear the field and save to restore the default prompt.")
                    .font(.caption)
                    .foregroundStyle(Color.textTertiary)
                Spacer()
                Button {
                    Task { await saveDigestPreferencePrompt() }
                } label: {
                    if isSavingDigestPreferencePrompt {
                        ProgressView()
                    } else {
                        Text("Save Preferences")
                            .font(.callout.weight(.semibold))
                    }
                }
                .disabled(isSavingDigestPreferencePrompt || !hasUnsavedDigestPreferencePromptEdits)
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

    private var longArticleDisplayModeRow: some View {
        VStack(alignment: .leading, spacing: 10) {
            let selectedMode = LongArticleDisplayMode(rawValue: settings.longArticleDisplayMode) ?? .both

            HStack(spacing: 12) {
                Image(systemName: "doc.text.magnifyingglass")
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(.indigo)
                    .frame(width: Spacing.iconSize, height: Spacing.iconSize)

                VStack(alignment: .leading, spacing: 2) {
                    Text("Long Article Format")
                        .font(.listTitle)
                        .foregroundStyle(Color.textPrimary)
                    Text(selectedMode.detail)
                        .font(.listCaption)
                        .foregroundStyle(Color.textTertiary)
                }
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

    private var authenticatedUser: User? {
        guard case .authenticated(let user) = authViewModel.authState else {
            return nil
        }
        return user
    }

    // MARK: - Actions

    @MainActor
    private func syncDigestIntervalWithAuthenticatedUser() {
        guard !isSavingDigestInterval else { return }
        selectedDigestIntervalHours = authenticatedUser?.newsDigestIntervalHours
            ?? NewsDigestIntervalOption.every6Hours.rawValue
    }

    @MainActor
    private func syncDigestPreferencePromptWithAuthenticatedUser(force: Bool) {
        guard !isSavingDigestPreferencePrompt else { return }
        guard let user = authenticatedUser else {
            serverDigestPreferencePrompt = ""
            if force || !isDigestPreferencePromptFocused {
                digestPreferencePromptDraft = ""
            }
            hasUnsavedDigestPreferencePromptEdits = false
            return
        }

        serverDigestPreferencePrompt = user.newsDigestPreferencePrompt
        if force || (!isDigestPreferencePromptFocused && !hasUnsavedDigestPreferencePromptEdits) {
            digestPreferencePromptDraft = user.newsDigestPreferencePrompt
        }
        hasUnsavedDigestPreferencePromptEdits =
            normalizedDigestPreferencePromptForComparison(digestPreferencePromptDraft)
            != normalizedDigestPreferencePromptForComparison(serverDigestPreferencePrompt)
    }

    @MainActor
    private func saveDigestIntervalHours(_ intervalHours: Int) async {
        guard !isSavingDigestInterval, authenticatedUser != nil else { return }
        isSavingDigestInterval = true
        defer { isSavingDigestInterval = false }

        do {
            let user = try await AuthenticationService.shared.updateCurrentUserProfile(
                newsDigestIntervalHours: intervalHours
            )
            authViewModel.updateUser(user)
            selectedDigestIntervalHours = user.newsDigestIntervalHours
            alertMessage = "Digest frequency saved."
            showingAlert = true
        } catch {
            if let user = authenticatedUser {
                selectedDigestIntervalHours = user.newsDigestIntervalHours
            }
            alertMessage = "Failed to save digest frequency: \(error.localizedDescription)"
            showingAlert = true
        }
    }

    private func normalizedDigestPreferencePromptDraft() -> String? {
        let trimmed = digestPreferencePromptDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func normalizedDigestPreferencePromptForComparison(_ value: String) -> String {
        value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    @MainActor
    private func saveDigestPreferencePrompt() async {
        guard !isSavingDigestPreferencePrompt, authenticatedUser != nil else { return }
        isSavingDigestPreferencePrompt = true
        defer { isSavingDigestPreferencePrompt = false }

        do {
            let user = try await AuthenticationService.shared.updateCurrentUserProfile(
                newsDigestPreferencePrompt: normalizedDigestPreferencePromptDraft()
            )
            authViewModel.updateUser(user)
            serverDigestPreferencePrompt = user.newsDigestPreferencePrompt
            digestPreferencePromptDraft = user.newsDigestPreferencePrompt
            hasUnsavedDigestPreferencePromptEdits = false
            alertMessage = "Digest preferences saved."
            showingAlert = true
        } catch {
            alertMessage = "Failed to save digest preferences: \(error.localizedDescription)"
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
