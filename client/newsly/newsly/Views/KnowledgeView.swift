//
//  KnowledgeView.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import SwiftUI

enum KnowledgeFocusTarget: Hashable {
    case narrations
}

struct KnowledgeFocusRequest: Equatable {
    let id = UUID()
    let target: KnowledgeFocusTarget
}

struct KnowledgeView: View {
    let focusRequest: KnowledgeFocusRequest?
    let onFocusHandled: ((KnowledgeFocusRequest) -> Void)?
    let onSelectSession: ((ChatSessionRoute) -> Void)?
    let onShowKnowledgeLibrary: (() -> Void)?

    @StateObject private var viewModel = KnowledgeHubViewModel()
    @StateObject private var narrationPlaybackService = NarrationPlaybackService.shared
    @ObservedObject private var settings = AppSettings.shared
    @State private var searchText = ""
    @State private var customNarrations: [AudioEpisode] = []
    @State private var isLoadingCustomNarrations = false
    @State private var customNarrationError: String?
    @State private var showNarrationList = false
    @FocusState private var isSearchFocused: Bool

    private let primaryAction = HubAction(
        icon: "doc.text.magnifyingglass",
        title: "Today's Summary",
        subtitle: "Recap of the last day's content",
        run: { viewModel in await viewModel.startSummaryChat() }
    )

    private let secondaryActions: [HubAction] = [
        HubAction(
            icon: "bubble.left.and.text.bubble.right",
            title: "Top Comments",
            subtitle: "Most interesting discussions",
            run: { viewModel in await viewModel.startCommentsChat() }
        ),
        HubAction(
            icon: "newspaper.fill",
            title: "Find Articles",
            subtitle: "Fresh reads based on your history",
            run: { viewModel in await viewModel.startFindArticlesChat() }
        ),
        HubAction(
            icon: "dot.radiowaves.left.and.right",
            title: "Find Feeds",
            subtitle: "Sources and podcasts to add next",
            run: { viewModel in await viewModel.startFindFeedsChat() }
        ),
    ]

    private var appTextSize: DynamicTypeSize {
        AppTextSize(index: settings.appTextSizeIndex).dynamicTypeSize
    }

    init(
        focusRequest: KnowledgeFocusRequest? = nil,
        onFocusHandled: ((KnowledgeFocusRequest) -> Void)? = nil,
        onSelectSession: ((ChatSessionRoute) -> Void)? = nil,
        onShowKnowledgeLibrary: (() -> Void)? = nil
    ) {
        self.focusRequest = focusRequest
        self.onFocusHandled = onFocusHandled
        self.onSelectSession = onSelectSession
        self.onShowKnowledgeLibrary = onShowKnowledgeLibrary
    }

    var body: some View {
        ScrollViewReader { scrollProxy in
            ScrollView {
                LazyVStack(spacing: 0) {
                    headerSection
                    searchFieldSection
                    errorBannerSection
                    savedAndNarrationsSection
                        .id(KnowledgeFocusTarget.narrations)
                    actionsSection
                    chatHistorySection
                }
                .padding(.bottom, 32)
            }
            .refreshable {
                await loadKnowledgeScreen()
            }
            .onAppear {
                scrollToFocusRequest(focusRequest, proxy: scrollProxy)
            }
            .onChange(of: focusRequest) { _, request in
                scrollToFocusRequest(request, proxy: scrollProxy)
            }
        }
        .safeAreaInset(edge: .bottom, alignment: .trailing, spacing: 0) {
            newChatMicButton
                .padding(.trailing, 20)
                .padding(.bottom, 12)
        }
        .dynamicTypeSize(appTextSize)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $showNarrationList) {
            CustomNarrationListSheet(
                episodes: customNarrations,
                isLoading: isLoadingCustomNarrations,
                errorMessage: customNarrationError,
                playbackService: narrationPlaybackService,
                onRefresh: {
                    await loadCustomNarrations()
                },
                onTapEpisode: { episode in
                    await handleCustomNarrationTap(episode)
                },
                isEpisodePlaying: { episode in
                    isCustomNarrationPlaying(episode)
                },
                subtitle: { episode in
                    customNarrationSubtitle(episode)
                }
            )
        }
        .task {
            await loadKnowledgeScreen()
        }
    }

    // MARK: - Header

    private var headerSection: some View {
        EditorialMastheadHeader(title: "Knowledge")
    }

    // MARK: - Search Field

    private var searchFieldSection: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 16, weight: .medium))
                .foregroundColor(.onSurfaceSecondary)

            TextField("Ask anything...", text: $searchText)
                .font(.terracottaBodyLarge)
                .focused($isSearchFocused)
                .submitLabel(.send)
                .onSubmit {
                    sendSearchQuery()
                }

            if !searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Button {
                    sendSearchQuery()
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 28))
                        .foregroundColor(viewModel.isCreatingSession ? .onSurfaceSecondary : .terracottaPrimary)
                }
                .disabled(viewModel.isCreatingSession)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(Color.surfaceContainer)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .padding(.horizontal, Spacing.screenHorizontal)
        .padding(.bottom, 24)
    }

    private var errorBannerSection: some View {
        Group {
            if let errorMessage = viewModel.errorMessage {
                Text(errorMessage)
                    .font(.terracottaBodySmall)
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                    .background(Color.red.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                    .padding(.horizontal, Spacing.screenHorizontal)
                    .padding(.bottom, 24)
            }
        }
    }

    // MARK: - Saved and Narrations

    private var savedAndNarrationsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Library")
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(Color.onSurface)
                .padding(.horizontal, Spacing.screenHorizontal)

            LazyVGrid(columns: twoColumnGrid, spacing: 10) {
                libraryButton(
                    title: "Saved",
                    systemImage: "books.vertical.fill",
                    action: {
                        onShowKnowledgeLibrary?()
                    }
                )
                .disabled(onShowKnowledgeLibrary == nil)

                libraryButton(
                    title: "Narration",
                    systemImage: "waveform",
                    action: {
                        showNarrationList = true
                    }
                )
            }
            .padding(.horizontal, Spacing.screenHorizontal)
        }
        .padding(.bottom, 22)
    }

    private var twoColumnGrid: [GridItem] {
        [
            GridItem(.flexible(), spacing: 10),
            GridItem(.flexible(), spacing: 10),
        ]
    }

    private func libraryButton(
        title: String,
        systemImage: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                Image(systemName: systemImage)
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(Color.terracottaPrimary)
                    .frame(width: 40, height: 40)
                    .background(Color.terracottaPrimary.opacity(0.14))
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

                HStack(alignment: .lastTextBaseline, spacing: 8) {
                    Text(title)
                        .font(.terracottaHeadlineSmall)
                        .foregroundStyle(Color.onSurface)
                        .lineLimit(1)
                        .minimumScaleFactor(0.85)

                    Spacer(minLength: 0)

                    Image(systemName: "arrow.right")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 14)
            .frame(maxWidth: .infinity, minHeight: 104, alignment: .topLeading)
            .background(Color.surfaceSecondary)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.3), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    private func scrollToFocusRequest(
        _ request: KnowledgeFocusRequest?,
        proxy: ScrollViewProxy
    ) {
        guard let request else { return }

        DispatchQueue.main.async {
            withAnimation(.easeInOut(duration: 0.25)) {
                proxy.scrollTo(request.target, anchor: .top)
            }
            onFocusHandled?(request)
        }
    }

    @ViewBuilder
    private func customNarrationIcon(_ episode: AudioEpisode) -> some View {
        if episode.isGenerating {
            ProgressView()
                .controlSize(.small)
        } else if episode.isFailed {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.red)
        } else {
            Image(systemName: isCustomNarrationPlaying(episode) ? "speaker.wave.3.fill" : "waveform")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Color.terracottaPrimary)
        }
    }

    private func customNarrationSubtitle(_ episode: AudioEpisode) -> String {
        let sourceText: String
        if episode.sourceCount == 1 {
            sourceText = "1 source"
        } else {
            sourceText = "\(episode.sourceCount) sources"
        }

        if episode.isGenerating {
            return "\(sourceText) • Generating"
        }
        if episode.isFailed {
            return "\(sourceText) • \(episode.errorMessage ?? "Failed")"
        }
        if let duration = episode.durationSeconds {
            return "\(sourceText) • \(formattedNarrationDuration(duration))"
        }
        return sourceText
    }

    private func formattedNarrationDuration(_ seconds: Int) -> String {
        let minutes = max(Int(round(Double(seconds) / 60.0)), 1)
        return "\(minutes) min"
    }

    private func isCustomNarrationPlaying(_ episode: AudioEpisode) -> Bool {
        narrationPlaybackService.isSpeaking
            && narrationPlaybackService.speakingTarget == .audioEpisode(episode.id)
    }

    @MainActor
    private func handleCustomNarrationTap(_ episode: AudioEpisode) async {
        if isCustomNarrationPlaying(episode) {
            narrationPlaybackService.pause()
            return
        }

        if episode.isGenerating {
            await refreshCustomNarration(episode)
            return
        }

        guard episode.isCompleted else { return }

        do {
            try await narrationPlaybackService.playStreamingNarration(
                for: .audioEpisode(episode.id),
                fetchStreamResource: {
                    try await AudioEpisodeService.shared.streamResource(for: episode)
                }
            )
        } catch {
            customNarrationError = error.localizedDescription
        }
    }

    @MainActor
    private func refreshCustomNarration(_ episode: AudioEpisode) async {
        do {
            let latest = try await AudioEpisodeService.shared.fetchEpisode(id: episode.id)
            replaceCustomNarration(latest)
        } catch {
            customNarrationError = error.localizedDescription
        }
    }

    @MainActor
    private func replaceCustomNarration(_ episode: AudioEpisode) {
        if let index = customNarrations.firstIndex(where: { $0.id == episode.id }) {
            customNarrations[index] = episode
        }
    }

    // MARK: - Actions

    private var actionsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Actions")
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(Color.onSurface)
                .padding(.horizontal, Spacing.screenHorizontal)

            LazyVGrid(columns: twoColumnGrid, spacing: 10) {
                compactActionButton(primaryAction)
                ForEach(secondaryActions) { action in
                    compactActionButton(action)
                }
            }
            .padding(.horizontal, Spacing.screenHorizontal)
        }
        .padding(.bottom, 22)
    }

    private func compactActionButton(_ action: HubAction) -> some View {
        Button {
            startAction(action)
        } label: {
            VStack(alignment: .leading, spacing: 10) {
                actionIcon(action.icon, size: 40, iconSize: 17, cornerRadius: 12)

                VStack(alignment: .leading, spacing: 4) {
                    Text(action.title)
                        .font(.terracottaBodyLarge.weight(.semibold))
                        .foregroundColor(.onSurface)
                        .lineLimit(2)
                        .minimumScaleFactor(0.84)
                        .fixedSize(horizontal: false, vertical: true)

                    Text(action.subtitle)
                        .font(.terracottaBodySmall)
                        .foregroundColor(.onSurfaceSecondary)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 0)
            }
            .padding(12)
            .frame(maxWidth: .infinity, minHeight: 132, alignment: .topLeading)
            .background(Color.surfaceSecondary)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.3), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .disabled(viewModel.isCreatingSession)
    }

    private func actionIcon(
        _ systemName: String,
        size: CGFloat,
        iconSize: CGFloat,
        cornerRadius: CGFloat
    ) -> some View {
        Image(systemName: systemName)
            .font(.system(size: iconSize, weight: .semibold))
            .foregroundColor(.terracottaPrimary)
            .frame(width: size, height: size)
            .background(Color.terracottaPrimary.opacity(0.14))
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
    }

    private func startAction(_ action: HubAction) {
        Task {
            if let route = await action.run(viewModel) {
                onSelectSession?(route)
            }
        }
    }

    // MARK: - Chat History

    private var chatHistorySection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Recent Chats")
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(Color.onSurface)
                .padding(.horizontal, Spacing.screenHorizontal)

            if viewModel.isLoading && viewModel.sessions.isEmpty {
                chatHistoryLoadingRow
            } else if viewModel.sessions.isEmpty {
                chatHistoryEmptyRow
            } else {
                VStack(spacing: 10) {
                    ForEach(viewModel.sessions) { session in
                        Button {
                            onSelectSession?(ChatSessionRoute(session: session))
                        } label: {
                            ChatSessionCard(session: session)
                        }
                        .buttonStyle(.plain)
                        .padding(.horizontal, Spacing.screenHorizontal)
                    }
                }

                chatHistoryFooter
            }
        }
        .padding(.bottom, 32)
    }

    private var chatHistoryLoadingRow: some View {
        HStack(spacing: 10) {
            ProgressView()
            Text("Loading chats")
                .font(.terracottaBodyMedium)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 18)
        .padding(.horizontal, Spacing.screenHorizontal)
    }

    private var chatHistoryEmptyRow: some View {
        Text("No chats yet")
            .font(.terracottaBodyMedium)
            .foregroundStyle(Color.onSurfaceSecondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Spacing.screenHorizontal)
            .padding(.vertical, 8)
    }

    private var chatHistoryFooter: some View {
        Group {
            if viewModel.isLoadingMore {
                ProgressView()
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
            } else if viewModel.hasLoadMoreError {
                Button {
                    Task { await viewModel.loadMoreSessions() }
                } label: {
                    Label("Retry", systemImage: "arrow.clockwise")
                        .font(.terracottaBodyMedium)
                        .foregroundStyle(Color.terracottaPrimary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                }
                .buttonStyle(.plain)
                .padding(.horizontal, Spacing.screenHorizontal)
            } else if viewModel.hasMoreSessions {
                Button {
                    Task { await viewModel.loadMoreSessions() }
                } label: {
                    Label("Load more", systemImage: "chevron.down")
                        .font(.terracottaBodyMedium.weight(.semibold))
                        .foregroundStyle(Color.terracottaPrimary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                }
                .buttonStyle(.plain)
                .padding(.horizontal, Spacing.screenHorizontal)
            }
        }
    }

    // MARK: - Actions

    private var newChatMicButton: some View {
        TapToTalkMicButton(
            isEnabled: !viewModel.isCreatingSession,
            isRecording: false,
            isBusy: viewModel.isCreatingSession,
            size: 60,
            action: {
                Task {
                    if let route = await viewModel.startNewChat() {
                        onSelectSession?(route)
                    }
                }
            }
        )
        .shadow(color: .black.opacity(0.22), radius: 12, y: 8)
        .accessibilityIdentifier("knowledge.new_chat_mic")
        .accessibilityLabel("New chat")
        .accessibilityHint("Start a new chat session")
    }

    private func sendSearchQuery() {
        let trimmed = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        isSearchFocused = false
        let query = trimmed
        searchText = ""

        Task {
            if let route = await viewModel.startSearchChat(message: query) {
                onSelectSession?(route)
            }
        }
    }

    @MainActor
    private func loadKnowledgeScreen() async {
        await viewModel.loadHub()
        await loadCustomNarrations()
    }

    @MainActor
    private func loadCustomNarrations() async {
        guard !isLoadingCustomNarrations else { return }
        isLoadingCustomNarrations = true
        defer { isLoadingCustomNarrations = false }

        do {
            customNarrations = try await AudioEpisodeService.shared.fetchCustomNarrationEpisodes()
            customNarrationError = nil
        } catch {
            customNarrationError = error.localizedDescription
        }
    }
}

private struct CustomNarrationListSheet: View {
    let episodes: [AudioEpisode]
    let isLoading: Bool
    let errorMessage: String?
    @ObservedObject var playbackService: NarrationPlaybackService
    let onRefresh: () async -> Void
    let onTapEpisode: (AudioEpisode) async -> Void
    let isEpisodePlaying: (AudioEpisode) -> Bool
    let subtitle: (AudioEpisode) -> String

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if let errorMessage {
                    Text(errorMessage)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(.red)
                        .appListRow()
                }

                if isLoading && episodes.isEmpty {
                    HStack(spacing: 10) {
                        ProgressView()
                            .controlSize(.small)
                        Text("Loading narrations")
                            .font(.terracottaBodyMedium)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                    .appListRow()
                } else if episodes.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("No narrations yet")
                            .font(.terracottaHeadlineSmall)
                            .foregroundStyle(Color.onSurface)
                        Text("Created narrations will show up here.")
                            .font(.terracottaBodySmall)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 10)
                    .appListRow()
                } else {
                    ForEach(episodes) { episode in
                        narrationRow(episode)
                            .appListRow()
                    }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(Color.surfacePrimary)
            .navigationTitle("Narration")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
            .refreshable {
                await onRefresh()
            }
        }
    }

    private func narrationRow(_ episode: AudioEpisode) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Button {
                Task {
                    await onTapEpisode(episode)
                }
            } label: {
                HStack(spacing: 12) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(Color.terracottaPrimary.opacity(0.14))
                            .frame(width: 38, height: 38)

                        narrationIcon(episode)
                    }

                    VStack(alignment: .leading, spacing: 3) {
                        Text(episode.title)
                            .font(.terracottaBodyLarge.weight(.semibold))
                            .foregroundStyle(Color.onSurface)
                            .lineLimit(2)

                        Text(subtitle(episode))
                            .font(.terracottaBodySmall)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .lineLimit(1)
                    }

                    Spacer(minLength: 10)

                    Image(systemName: isEpisodePlaying(episode) ? "pause.fill" : "play.fill")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(episode.isCompleted ? Color.terracottaPrimary : Color.onSurfaceSecondary)
                        .frame(width: 30, height: 30)
                        .background(Color.surfaceSecondary)
                        .clipShape(Circle())
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if episode.isCompleted && isEpisodePlaying(episode) {
                NarrationPlaybackControlRow(
                    playbackService: playbackService,
                    target: .audioEpisode(episode.id),
                    isPreparing: false,
                    onTogglePlayback: {
                        Task {
                            await onTapEpisode(episode)
                        }
                    }
                )
            }
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, 9)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("knowledge.narration.\(episode.id)")
    }

    @ViewBuilder
    private func narrationIcon(_ episode: AudioEpisode) -> some View {
        if episode.isGenerating {
            ProgressView()
                .controlSize(.small)
        } else if episode.isFailed {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.red)
        } else {
            Image(systemName: isEpisodePlaying(episode) ? "speaker.wave.3.fill" : "waveform")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Color.terracottaPrimary)
        }
    }
}

private struct HubAction: Identifiable {
    let id = UUID()
    let icon: String
    let title: String
    let subtitle: String
    let run: @MainActor (KnowledgeHubViewModel) async -> ChatSessionRoute?
}

#Preview {
    KnowledgeView()
}
