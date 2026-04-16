import SwiftUI

struct ChatSessionView: View {
    @EnvironmentObject private var authViewModel: AuthenticationViewModel
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var viewModel: ChatSessionViewModel
    let onShowHistory: (() -> Void)?
    @FocusState private var isInputFocused: Bool
    @State private var shareContent: ShareContent?
    @State private var scrollToBottomRequest = 0
    @State private var isContextPanelPresented = false
    @State private var isCouncilSettingsPresented = false
    private let route: ChatSessionRoute
    private let dependencies: ChatDependencies

    @MainActor
    init(
        route: ChatSessionRoute,
        dependencies: ChatDependencies? = nil,
        onShowHistory: (() -> Void)? = nil
    ) {
        let resolvedDependencies = dependencies ?? .live
        self.route = route
        self.dependencies = resolvedDependencies
        _viewModel = State(initialValue: ChatSessionViewModel(route: route, dependencies: resolvedDependencies))
        self.onShowHistory = onShowHistory
    }

    private var defaultCouncilPrompt: String {
        if let title = viewModel.session?.articleTitle ?? viewModel.session?.displayTitle,
           !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Give me your perspective on \(title). Keep it short: 2-4 concise bullets on what matters, what is weak or missing, and what follows."
        }
        return "Give me your perspective on this conversation. Keep it short: 2-4 concise bullets on what matters, what is weak or missing, and what follows."
    }

    private var usesSplitContextLayout: Bool { horizontalSizeClass == .regular }

    var body: some View {
        GeometryReader { geometry in
            chatShell(width: geometry.size.width)
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            bottomDock
        }
        .sheet(isPresented: $isCouncilSettingsPresented) {
            NavigationStack {
                SettingsView(scrollToCouncilOnAppear: true)
                    .environmentObject(authViewModel)
            }
        }
        .scrollDismissesKeyboard(.interactively)
        .navigationBarTitleDisplayMode(.inline)
        .task(id: route.stableKey) {
            dependencies.activeSessionManager.stopTracking(sessionId: viewModel.sessionId)
            await viewModel.loadSession()
            await viewModel.checkAndRefreshVoiceDictation()
        }
        .onDisappear {
            viewModel.handleDisappear()
        }
        .toolbar {
            ChatSessionToolbarContent(
                session: viewModel.session,
                onOpenArticle: openArticle,
                onShowHistory: onShowHistory,
                onSwitchProvider: switchProvider
            )
        }
        .sheet(item: $shareContent) { content in
            ShareSheet(content: content)
        }
        .sheet(
            isPresented: Binding(
                get: { isContextPanelPresented && !usesSplitContextLayout },
                set: { isContextPanelPresented = $0 }
            )
        ) {
            secondaryPanel(isCompact: true)
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
        }
    }

    @ViewBuilder
    private func chatShell(width: CGFloat) -> some View {
        if usesSplitContextLayout && isContextPanelPresented {
            HStack(spacing: 0) {
                messageListView
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                Divider()

                secondaryPanel(isCompact: false)
                    .frame(width: min(360, max(280, width * 0.34)))
                    .background(Color.surfacePrimary)
            }
        } else {
            messageListView
        }
    }

    private func switchToProvider(_ provider: ChatModelProvider) async {
        guard let currentSession = viewModel.session else { return }

        do {
            let updatedSession = try await dependencies.chatService.updateSessionProvider(
                sessionId: currentSession.id,
                provider: provider
            )

            viewModel.updateSession(updatedSession)
        } catch {
            viewModel.errorMessage = "Failed to switch model: \(error.localizedDescription)"
        }
    }

    private func openArticle(_ urlString: String) {
        guard let url = URL(string: urlString) else { return }
        UIApplication.shared.open(url)
    }

    private var messageListView: some View {
        ChatMessageList(
            timeline: viewModel.timeline,
            hasMessages: !viewModel.allMessages.isEmpty,
            isLoading: viewModel.isLoading,
            errorMessage: viewModel.errorMessage,
            isStartingCouncil: viewModel.isStartingCouncil,
            isSending: viewModel.isSending,
            thinkingElapsedSeconds: viewModel.thinkingElapsedSeconds,
            latestProcessSummary: viewModel.latestProcessSummary,
            session: viewModel.session,
            scrollToBottomRequest: scrollToBottomRequest,
            retryingCouncilChildSessionId: viewModel.retryingCouncilChildSessionId,
            onOpenCouncilSettings: openCouncilSettings,
            onDismissError: dismissError,
            onRetryLoad: retryLoad,
            onRetrySend: retrySend,
            onRetryCouncilCandidate: retryCouncilCandidate,
            onDigDeeper: digDeeper,
            onShare: presentShareSheet
        )
    }

    private func presentShareSheet(for content: String) {
        shareContent = ShareContent(
            messageContent: content,
            articleTitle: viewModel.session?.articleTitle,
            articleUrl: viewModel.session?.articleUrl
        )
    }

    private var bottomDock: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !viewModel.councilCandidates.isEmpty {
                councilBranchSwitcher
                    .padding(.horizontal, 16)
            }

            composerDock
        }
        .padding(.top, 10)
        .padding(.bottom, 8)
        .background(
            LinearGradient(
                stops: [
                    .init(color: Color.surfacePrimary.opacity(0), location: 0),
                    .init(color: Color.surfacePrimary.opacity(0.96), location: 0.18),
                    .init(color: Color.surfacePrimary, location: 1.0),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
        )
    }

    private var composerDock: some View {
        ChatComposerDock(
            inputText: $viewModel.inputText,
            isInputFocused: $isInputFocused,
            contextTitle: usesSplitContextLayout && isContextPanelPresented ? "Hide Context" : "Context",
            isContextPresented: isContextPanelPresented,
            canStartCouncil: viewModel.canStartCouncil,
            isStartingCouncil: viewModel.isStartingCouncil,
            isSending: viewModel.isSending,
            isRecording: viewModel.isRecording,
            isTranscribing: viewModel.isTranscribing,
            isVoiceActionInFlight: viewModel.isVoiceActionInFlight,
            voiceDictationAvailable: viewModel.voiceDictationAvailable,
            providerName: viewModel.session?.providerDisplayName,
            onToggleContext: toggleContextPanel,
            onStartCouncil: startCouncil,
            onToggleVoiceRecording: toggleVoiceRecording,
            onSend: sendMessage
        )
    }

    private var councilBranchSwitcher: some View {
        CouncilBranchTabs(
            candidates: viewModel.councilCandidates,
            activeChildSessionId: viewModel.activeCouncilChildSessionId,
            selectingChildSessionId: viewModel.selectingCouncilChildSessionId,
            hasSelectionTimedOut: viewModel.councilSelectionTimedOut,
            onSelect: selectCouncilCandidate,
            onCancelSelection: cancelCouncilSelection
        )
    }

    private func toggleContextPanel() {
        if usesSplitContextLayout {
            withAnimation(.easeInOut(duration: 0.2)) {
                isContextPanelPresented.toggle()
            }
        } else {
            isContextPanelPresented = true
        }
    }

    @ViewBuilder
    private func secondaryPanel(isCompact: Bool) -> some View {
        ChatSecondaryPanel(
            session: viewModel.session,
            activeCouncilCandidate: viewModel.activeCouncilCandidate,
            onOpenArticle: openArticle
        )
        .padding(isCompact ? 0 : 16)
    }
}

private extension ChatSessionView {
    func switchProvider(_ provider: ChatModelProvider) {
        Task { await switchToProvider(provider) }
    }

    func openCouncilSettings() { isCouncilSettingsPresented = true }

    func dismissError() { viewModel.errorMessage = nil }

    func retryLoad() { Task { await viewModel.loadSession() } }

    func retrySend(_ text: String) {
        scrollToBottomRequest += 1
        viewModel.performSendMessage(text: text)
    }

    func retryCouncilCandidate(_ candidate: CouncilCandidate) {
        scrollToBottomRequest += 1
        viewModel.performRetryCouncilCandidate(childSessionId: candidate.childSessionId)
    }

    func digDeeper(into selectedText: String) {
        viewModel.performDigDeeper(into: selectedText)
    }

    func startCouncil() {
        scrollToBottomRequest += 1
        viewModel.performStartCouncil(message: defaultCouncilPrompt)
    }

    func toggleVoiceRecording() { viewModel.performToggleVoiceRecording() }

    func sendMessage() {
        scrollToBottomRequest += 1
        viewModel.performSendMessage()
    }

    func selectCouncilCandidate(_ candidate: CouncilCandidate) {
        guard viewModel.activeCouncilChildSessionId != candidate.childSessionId else { return }
        Task {
            await viewModel.selectCouncilBranch(childSessionId: candidate.childSessionId)
            scrollToBottomRequest += 1
        }
    }

    func cancelCouncilSelection() { viewModel.cancelCouncilSelection() }
}
