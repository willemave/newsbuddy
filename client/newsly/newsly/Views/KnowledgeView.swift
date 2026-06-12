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
    @StateObject private var learningDecksViewModel = LearningDecksViewModel()
    @ObservedObject private var settings = AppSettings.shared
    @State private var searchText = ""
    @State private var showNarrationList = false
    @State private var showLearningDeckList = false
    @State private var runningActionID: HubActionID?
    @FocusState private var isSearchFocused: Bool

    private let primaryAction = HubAction(
        id: .summary,
        icon: "doc.text.magnifyingglass",
        title: "Today's Summary",
        run: { viewModel in await viewModel.startSummaryChat() }
    )

    private let secondaryActions: [HubAction] = [
        HubAction(
            id: .topComments,
            icon: "bubble.left.and.text.bubble.right",
            title: "Top Comments",
            run: { viewModel in await viewModel.startCommentsChat() }
        ),
        HubAction(
            id: .findArticles,
            icon: "newspaper.fill",
            title: "Find Articles",
            run: { viewModel in await viewModel.startFindArticlesChat() }
        ),
        HubAction(
            id: .findFeeds,
            icon: "dot.radiowaves.left.and.right",
            title: "Find Feeds",
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
            .accessibilityIdentifier("knowledge.screen")
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
        .topScreenEdgeFade()
        .dynamicTypeSize(appTextSize)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $showNarrationList) {
            CustomNarrationListSheet(
                viewModel: customNarrationLibrary,
                playbackService: customNarrationLibrary.playbackService
            )
        }
        .sheet(isPresented: $showLearningDeckList) {
            learningDeckListSheet
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

    // MARK: - Learning Decks

    private var learningDeckListSheet: some View {
        LearningDeckListSheet(
            viewModel: learningDecksViewModel,
            isPresented: $showLearningDeckList
        )
    }

    // MARK: - Header

    private var headerSection: some View {
        EditorialMastheadHeader(title: "Knowledge")
    }

    // MARK: - Section Headers

    private func sectionHeader(_ title: String) -> some View {
        Text(title.uppercased())
            .kicker()
            .accessibilityLabel(title)
            .padding(.horizontal, Spacing.appHorizontalMargin)
    }

    // MARK: - Search Field

    private var trimmedSearchText: String {
        searchText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var searchFieldSection: some View {
        HStack(spacing: 10) {
            Image(systemName: "text.bubble")
                .font(.appSymbol(size: 16, weight: .medium))
                .foregroundColor(.onSurfaceSecondary)
                .accessibilityHidden(true)

            TextField("Ask anything...", text: $searchText)
                .font(.terracottaBodyLarge)
                .focused($isSearchFocused)
                .submitLabel(.send)
                .onSubmit {
                    sendSearchQuery()
                }
                .padding(.vertical, 11)
                .padding(.horizontal, 4)
                .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
                .background(Color.surfaceContainer.opacity(0.001))
                .accessibilityLabel("Ask Knowledge")
                .accessibilityIdentifier("knowledge.search.input")

            if !trimmedSearchText.isEmpty && !viewModel.isVoiceRecording {
                Button {
                    sendSearchQuery()
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.appSymbol(size: 28))
                        .foregroundColor(viewModel.isCreatingSession ? .onSurfaceSecondary : .terracottaPrimary)
                        .frame(width: 44, height: 44)
                }
                .disabled(viewModel.isCreatingSession)
                .contentShape(Circle())
                .accessibilityLabel("Send question")
                .accessibilityIdentifier("knowledge.search.send")
            } else {
                composerMicButton
            }
        }
        .padding(.leading, 16)
        .padding(.trailing, 8)
        .background(Color.surfaceContainer)
        .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.bottom, 24)
    }

    private var composerMicButton: some View {
        TapToTalkMicButton(
            isEnabled: viewModel.isVoiceRecording ||
                (!viewModel.isCreatingSession && !viewModel.isVoiceActionInFlight && !viewModel.isVoiceTranscribing),
            isRecording: viewModel.isVoiceRecording,
            isBusy: !viewModel.isVoiceRecording &&
                (viewModel.isCreatingSession || viewModel.isVoiceActionInFlight || viewModel.isVoiceTranscribing),
            size: 36,
            action: {
                Task {
                    if let route = await viewModel.toggleVoiceRecording() {
                        onSelectSession?(route)
                    }
                }
            }
        )
        .frame(width: 44, height: 44)
        .opacity(viewModel.voiceDictationAvailable || viewModel.isVoiceRecording ? 1 : 0.72)
        .accessibilityIdentifier("knowledge.new_chat_mic")
        .accessibilityLabel(viewModel.isVoiceRecording ? "Stop voice chat" : "Start voice chat")
        .accessibilityHint(viewModel.isVoiceRecording ? "Stop recording and start a chat" : "Record a question and start a chat")
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
                    .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
                    .padding(.horizontal, Spacing.appHorizontalMargin)
                    .padding(.bottom, 24)
            }
        }
    }

    // MARK: - Saved and Narrations

    private var savedAndNarrationsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionHeader("Library")

            HStack(spacing: 8) {
                libraryTile(
                    title: "Saved",
                    systemImage: "books.vertical.fill",
                    action: {
                        onShowKnowledgeLibrary?()
                    }
                )
                .disabled(onShowKnowledgeLibrary == nil)

                libraryTile(
                    title: "Narration",
                    systemImage: "waveform",
                    action: {
                        showNarrationList = true
                    }
                )

                libraryTile(
                    title: "Learning Decks",
                    systemImage: "rectangle.stack.fill",
                    action: {
                        showLearningDeckList = true
                    }
                )
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
        }
        .padding(.bottom, 24)
    }

    private func libraryTile(
        title: String,
        systemImage: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top) {
                    actionIcon(systemImage)

                    Spacer(minLength: 0)

                    Image(systemName: "arrow.right")
                        .font(.appSymbol(size: 11, weight: .semibold))
                        .foregroundStyle(Color.onSurfaceSecondary)
                }

                Text(title)
                    .font(.terracottaBodyMedium.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
            }
            .padding(12)
            .frame(maxWidth: .infinity, minHeight: 84, alignment: .topLeading)
            .background(Color.surfaceSecondary)
            .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
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
            if request.target == .narrations {
                showNarrationList = true
            }
            onFocusHandled?(request)
        }
    }

    // MARK: - Actions

    private var actionsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionHeader("Actions")

            primaryActionCard

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(secondaryActions) { action in
                        secondaryActionChip(action)
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
            }
        }
        .padding(.bottom, 24)
    }

    private var primaryActionCard: some View {
        let isRunning = runningActionID == primaryAction.id

        return Button {
            startAction(primaryAction)
        } label: {
            HStack(spacing: 12) {
                actionIcon(primaryAction.icon, size: 36, iconSize: 16, isRunning: isRunning)

                VStack(alignment: .leading, spacing: 2) {
                    Text(primaryAction.title)
                        .font(.terracottaHeadlineSmall)
                        .foregroundStyle(Color.onSurface)

                    Text("The last day across your feed")
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }

                Spacer(minLength: 0)

                Image(systemName: "arrow.right")
                    .font(.appSymbol(size: 13, weight: .semibold))
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.surfaceSecondary)
            .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.3), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .allowsHitTesting(!viewModel.isCreatingSession && runningActionID == nil)
        .accessibilityValue(isRunning ? "Starting" : "")
    }

    private func secondaryActionChip(_ action: HubAction) -> some View {
        let isRunning = runningActionID == action.id

        return Button {
            startAction(action)
        } label: {
            FeedActionChip(
                title: action.title,
                systemImage: action.icon,
                isLoading: isRunning
            )
        }
        .buttonStyle(EditorialCardButtonStyle())
        .allowsHitTesting(!viewModel.isCreatingSession && runningActionID == nil)
        .accessibilityValue(isRunning ? "Starting" : "")
    }

    private func actionIcon(
        _ systemName: String,
        size: CGFloat = 32,
        iconSize: CGFloat = 15,
        isRunning: Bool = false
    ) -> some View {
        ZStack {
            if isRunning {
                ProgressView()
                    .controlSize(.small)
                    .tint(Color.brandPrimary)
            } else {
                Image(systemName: systemName)
                    .font(.appSymbol(size: iconSize, weight: .semibold))
                    .foregroundColor(.brandPrimary)
            }
        }
        .frame(width: size, height: size)
        .background(Color.brandPrimary.opacity(0.14))
        .clipShape(RoundedRectangle(cornerRadius: CornerRadius.nestedControl, style: .continuous))
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
            sectionHeader("Recent Chats")

            if viewModel.isLoading && viewModel.sessions.isEmpty {
                chatHistoryLoadingRow
            } else if viewModel.sessions.isEmpty {
                chatHistoryEmptyRow
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(chatDayGroups) { group in
                        chatDayDelimiter(group.label)

                        ForEach(group.sessions) { session in
                            Button {
                                onSelectSession?(ChatSessionRoute(session: session))
                            } label: {
                                ChatSessionCard(session: session)
                            }
                            .buttonStyle(.plain)
                            .padding(.horizontal, Spacing.appHorizontalMargin)
                        }
                    }
                }

                chatHistoryFooter
            }
        }
    }

    private struct ChatDayGroup: Identifiable {
        let id: String
        let label: String
        var sessions: [ChatSessionSummary]
    }

    private static let chatDayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d"
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    private var chatDayGroups: [ChatDayGroup] {
        var groups: [ChatDayGroup] = []
        for session in viewModel.sessions {
            let label = chatDayLabel(for: session.lastActivityDate)
            if groups.last?.label == label {
                groups[groups.count - 1].sessions.append(session)
            } else {
                groups.append(ChatDayGroup(id: label, label: label, sessions: [session]))
            }
        }
        return groups
    }

    private func chatDayLabel(for date: Date?) -> String {
        guard let date else { return "EARLIER" }
        let calendar = Calendar.current

        if calendar.isDateInToday(date) {
            return "TODAY"
        } else if calendar.isDateInYesterday(date) {
            return "YESTERDAY"
        }
        return Self.chatDayFormatter.string(from: date).uppercased()
    }

    private func chatDayDelimiter(_ label: String) -> some View {
        HStack(spacing: 10) {
            Text(label)
                .kicker(color: .sectionDelimiter)

            Rectangle()
                .fill(Color.outlineVariant)
                .frame(height: 1)
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 2)
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
        .padding(.horizontal, Spacing.appHorizontalMargin)
    }

    private var chatHistoryEmptyRow: some View {
        Text("No chats yet")
            .font(.terracottaBodyMedium)
            .foregroundStyle(Color.onSurfaceSecondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Spacing.appHorizontalMargin)
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
                .padding(.horizontal, Spacing.appHorizontalMargin)
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
                .padding(.horizontal, Spacing.appHorizontalMargin)
            }
        }
    }

    private func sendSearchQuery() {
        let trimmed = trimmedSearchText
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
        await learningDecksViewModel.load()
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
    let run: @MainActor (KnowledgeHubViewModel) async -> ChatSessionRoute?
}

#Preview {
    KnowledgeView()
}
