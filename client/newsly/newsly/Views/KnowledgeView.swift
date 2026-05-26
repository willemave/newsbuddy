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
    @StateObject private var customNarrationLibrary = CustomNarrationLibraryViewModel()
    @ObservedObject private var settings = AppSettings.shared
    @State private var searchText = ""
    @State private var showNarrationList = false
    @State private var runningActionID: HubActionID?
    @FocusState private var isSearchFocused: Bool

    private let primaryAction = HubAction(
        id: .summary,
        icon: "doc.text.magnifyingglass",
        title: "Today's Summary",
        subtitle: "Recap of the last day's content",
        run: { viewModel in await viewModel.startSummaryChat() }
    )

    private let secondaryActions: [HubAction] = [
        HubAction(
            id: .topComments,
            icon: "bubble.left.and.text.bubble.right",
            title: "Top Comments",
            subtitle: "Most interesting discussions",
            run: { viewModel in await viewModel.startCommentsChat() }
        ),
        HubAction(
            id: .findArticles,
            icon: "newspaper.fill",
            title: "Find Articles",
            subtitle: "Fresh reads based on your history",
            run: { viewModel in await viewModel.startFindArticlesChat() }
        ),
        HubAction(
            id: .findFeeds,
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
                viewModel: customNarrationLibrary,
                playbackService: customNarrationLibrary.playbackService
            )
        }
        .onChange(of: viewModel.completedVoiceRoute) { _, route in
            guard let route else { return }
            viewModel.clearCompletedVoiceRoute()
            onSelectSession?(route)
        }
        .task {
            await loadKnowledgeScreen()
            await viewModel.checkAndRefreshVoiceDictation()
        }
        .onDisappear {
            viewModel.cancelVoiceRecording()
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
                    .foregroundStyle(Color.statusDestructive)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                    .background(Color.statusDestructive.opacity(0.08))
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
                    accent: .brandTertiary,
                    action: {
                        onShowKnowledgeLibrary?()
                    }
                )
                .disabled(onShowKnowledgeLibrary == nil)

                libraryButton(
                    title: "Narration",
                    systemImage: "waveform",
                    accent: .brandPrimary,
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
        accent: Color,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                Image(systemName: systemImage)
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(accent)
                    .frame(width: 40, height: 40)
                    .background(accent.opacity(0.14))
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
        let isRunning = runningActionID == action.id

        return Button {
            startAction(action)
        } label: {
            VStack(alignment: .leading, spacing: 10) {
                actionIcon(
                    action.icon,
                    accent: actionColor(for: action.id),
                    size: 40,
                    iconSize: 17,
                    cornerRadius: 12,
                    isRunning: isRunning
                )

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
        .allowsHitTesting(!viewModel.isCreatingSession && runningActionID == nil)
        .accessibilityValue(isRunning ? "Starting" : "")
    }

    private func actionIcon(
        _ systemName: String,
        accent: Color,
        size: CGFloat,
        iconSize: CGFloat,
        cornerRadius: CGFloat,
        isRunning: Bool = false
    ) -> some View {
        ZStack {
            if isRunning {
                ProgressView()
                    .controlSize(.small)
                    .tint(accent)
            } else {
                Image(systemName: systemName)
                    .font(.system(size: iconSize, weight: .semibold))
                    .foregroundColor(accent)
            }
        }
        .frame(width: size, height: size)
        .background(accent.opacity(0.14))
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
    }

    private func actionColor(for id: HubActionID) -> Color {
        switch id {
        case .summary:
            return .brandPrimary
        case .topComments:
            return .brandTertiary
        case .findArticles:
            return .brandSecondary
        case .findFeeds:
            return .brandSecondary
        }
    }

    private func startAction(_ action: HubAction) {
        guard !viewModel.isCreatingSession, runningActionID == nil else { return }

        runningActionID = action.id
        Task { @MainActor in
            defer { runningActionID = nil }
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

    // MARK: - New Chat

    private var newChatMicButton: some View {
        TapToTalkMicButton(
            isEnabled: viewModel.isVoiceRecording ||
                (!viewModel.isCreatingSession && !viewModel.isVoiceActionInFlight && !viewModel.isVoiceTranscribing),
            isRecording: viewModel.isVoiceRecording,
            isBusy: !viewModel.isVoiceRecording &&
                (viewModel.isCreatingSession || viewModel.isVoiceActionInFlight || viewModel.isVoiceTranscribing),
            size: 60,
            action: {
                Task {
                    if let route = await viewModel.toggleVoiceRecording() {
                        onSelectSession?(route)
                    }
                }
            }
        )
        .opacity(viewModel.voiceDictationAvailable || viewModel.isVoiceRecording ? 1 : 0.72)
        .shadow(color: .black.opacity(0.22), radius: 12, y: 8)
        .accessibilityIdentifier("knowledge.new_chat_mic")
        .accessibilityLabel(viewModel.isVoiceRecording ? "Stop voice chat" : "Start voice chat")
        .accessibilityHint(viewModel.isVoiceRecording ? "Stop recording and start a chat" : "Record a question and start a chat")
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
        await customNarrationLibrary.load()
    }
}

private enum HubActionID: Hashable {
    case summary
    case topComments
    case findArticles
    case findFeeds
}

private struct HubAction: Identifiable {
    let id: HubActionID
    let icon: String
    let title: String
    let subtitle: String
    let run: @MainActor (KnowledgeHubViewModel) async -> ChatSessionRoute?
}

#Preview {
    KnowledgeView()
}
