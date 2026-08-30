import SwiftUI
import UIKit

private enum ChatSessionDesign {
    static let edgeBackSwipeWidth: CGFloat = 28
    static let edgeBackSwipeThreshold: CGFloat = 80
}

private enum ChatSessionSheetDestination: Identifiable {
    case councilSettings
    case share(ShareContent)

    var id: String {
        switch self {
        case .councilSettings:
            "councilSettings"
        case .share(let content):
            "share.\(content.id.uuidString)"
        }
    }
}

struct ChatSessionView: View {
    @Environment(AppLifecycle.self) private var lifecycle
    @Environment(ActiveChatSessionManager.self) private var activeSessionManager

    private let route: ChatSessionRoute
    private let dependencies: ChatDependencies?
    private let onShowHistory: (() -> Void)?
    private let onClose: (() -> Void)?

    @MainActor
    init(
        route: ChatSessionRoute,
        dependencies: ChatDependencies? = nil,
        onShowHistory: (() -> Void)? = nil,
        onClose: (() -> Void)? = nil
    ) {
        self.route = route
        self.dependencies = dependencies
        self.onShowHistory = onShowHistory
        self.onClose = onClose
    }

    var body: some View {
        ChatSessionContent(
            route: route,
            lifecycle: lifecycle,
            dependencies: dependencies ?? .live(activeSessionManager: activeSessionManager),
            onShowHistory: onShowHistory,
            onClose: onClose
        )
    }
}

private struct ChatSessionContent: View {
    @Environment(AuthenticationViewModel.self) private var authViewModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.persistentBottomChromeInset) private var persistentBottomChromeInset

    private let lifecycle: AppLifecycle
    private let route: ChatSessionRoute
    private let dependencies: ChatDependencies
    private let onShowHistory: (() -> Void)?
    private let onClose: (() -> Void)?

    @State private var viewModel: ChatSessionViewModel
    @FocusState private var isInputFocused: Bool
    @State private var activeSheet: ChatSessionSheetDestination?
    @State private var scrollToBottomRequest = 0
    @State private var edgeBackDragOffset: CGFloat = 0
    @State private var edgeBackSwipeFeedbackTrigger = 0

    @MainActor
    init(
        route: ChatSessionRoute,
        lifecycle: AppLifecycle,
        dependencies: ChatDependencies,
        onShowHistory: (() -> Void)? = nil,
        onClose: (() -> Void)? = nil
    ) {
        self.route = route
        self.lifecycle = lifecycle
        self.dependencies = dependencies
        _viewModel = State(
            initialValue: ChatSessionViewModel(
                lifecycle: lifecycle,
                route: route,
                dependencies: dependencies
            )
        )
        self.onShowHistory = onShowHistory
        self.onClose = onClose
    }

    private var defaultCouncilPrompt: String {
        if let title = viewModel.session?.articleTitle ?? viewModel.session?.displayTitle,
           !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Give me your perspective on \(title). Keep it short: 2-4 concise bullets on what matters, what is weak or missing, and what follows."
        }
        return "Give me your perspective on this conversation. Keep it short: 2-4 concise bullets on what matters, what is weak or missing, and what follows."
    }

    var body: some View {
        VStack(spacing: 0) {
            chatHeader
            Divider()
            messageListView
                .overlay(alignment: .leading) {
                    edgeBackSwipeZone
                }
        }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                bottomDock
            }
            .offset(x: edgeBackDragOffset)
            .scrollDismissesKeyboard(.interactively)
            .navigationBarBackButtonHidden(true)
            .toolbar(.hidden, for: .navigationBar)
            .toolbar(.hidden, for: .tabBar)
            .task(id: route.stableKey) {
                viewModel.handleAppear()
                dependencies.activeSessionManager.stopTracking(sessionId: viewModel.sessionId)
                await viewModel.loadSession()
                await viewModel.checkAndRefreshVoiceDictation()
                if route.focusComposerOnAppear {
                    isInputFocused = true
                }
            }
            .onChange(of: lifecycle.phase) { _, _ in
                viewModel.handleLifecyclePhaseChange()
            }
            .task(id: lifecycle.activation?.generation) {
                await viewModel.resumeAfterActivationIfNeeded()
            }
            .onDisappear {
                viewModel.handleDisappear()
            }
            .sheet(item: $activeSheet) { destination in
                switch destination {
                case .councilSettings:
                    NavigationStack {
                        SettingsView(scrollToCouncilOnAppear: true)
                            .environment(authViewModel)
                    }
                case .share(let content):
                    ShareSheet(content: content)
                }
            }
            .sensoryFeedback(.impact(weight: .light), trigger: edgeBackSwipeFeedbackTrigger)
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

    private var messageListView: some View {
        ChatMessageList(
            timeline: viewModel.timeline,
            hasMessages: !viewModel.timeline.isEmpty,
            isLoading: viewModel.isLoading,
            loadErrorMessage: viewModel.loadErrorMessage,
            errorMessage: viewModel.errorMessage,
            isStartingCouncil: viewModel.isStartingCouncil,
            isSending: viewModel.isSending,
            thinkingStartedAt: viewModel.thinkingStartedAt,
            latestProcessSummary: viewModel.latestProcessSummary,
            hasVisiblePartialResponse: viewModel.hasVisiblePartialResponse,
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

    private var chatHeader: some View {
        HStack(spacing: 12) {
            FloatingBackButton(style: .surface) {
                closeChat()
            }

            VStack(alignment: .leading, spacing: 2) {
                Text((viewModel.session ?? route.session)?.displayTitle ?? "Chat")
                    .font(.appHeadline)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .accessibilityIdentifier("chat.header")

                if let session = viewModel.session ?? route.session {
                    Text(session.displaySubtitle ?? session.providerDisplayName)
                        .font(.appCaption)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 8)
        .background(Color.surfacePrimary.opacity(0.98))
        .accessibilityElement(children: .contain)
    }

    private var edgeBackSwipeZone: some View {
        Color.clear
            .frame(width: ChatSessionDesign.edgeBackSwipeWidth)
            .contentShape(Rectangle())
            .highPriorityGesture(edgeBackSwipeGesture)
            .accessibilityHidden(true)
    }

    private var edgeBackSwipeGesture: some Gesture {
        DragGesture(minimumDistance: 20, coordinateSpace: .global)
            .onChanged { value in
                guard isHorizontalBackSwipe(value) else { return }
                edgeBackDragOffset = min(value.translation.width * 0.45, 140)
            }
            .onEnded { value in
                guard isCompletedBackSwipe(value) else {
                    snapBackFromEdgeSwipe()
                    return
                }

                edgeBackSwipeFeedbackTrigger += 1
                let dismissWidth = activeScreenWidth
                withAnimation(AppMotion.subtle) {
                    edgeBackDragOffset = dismissWidth
                } completion: {
                    closeChat()
                    edgeBackDragOffset = 0
                }
            }
    }

    private func closeChat() {
        if let onClose {
            onClose()
        } else {
            dismiss()
        }
    }

    private func presentShareSheet(for content: String) {
        activeSheet = .share(
            ShareContent(
                messageContent: content,
                articleTitle: viewModel.session?.articleTitle,
                articleUrl: viewModel.session?.articleUrl
            )
        )
    }

    private var bottomDock: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !viewModel.councilCandidates.isEmpty {
                councilBranchSwitcher
                    .padding(.horizontal, Spacing.appHorizontalMargin)
            }

            composerDock
        }
        .padding(.top, 6)
        .padding(.bottom, 6)
        .padding(.bottom, persistentBottomChromeInset)
    }

    private var composerDock: some View {
        ChatComposerDock(
            inputText: $viewModel.inputText,
            isInputFocused: $isInputFocused,
            session: viewModel.session,
            canStartCouncil: viewModel.canStartCouncil,
            canStartDeepResearch: viewModel.canStartDeepResearch,
            isStartingCouncil: viewModel.isStartingCouncil,
            isSending: viewModel.isSending,
            isRecording: viewModel.isRecording,
            isTranscribing: viewModel.isTranscribing,
            isVoiceActionInFlight: viewModel.isVoiceActionInFlight,
            voiceDictationAvailable: viewModel.voiceDictationAvailable,
            onShowHistory: onShowHistory,
            onSwitchProvider: switchProvider,
            onStartCouncil: startCouncil,
            onStartDeepResearch: startDeepResearch,
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
}

private extension ChatSessionContent {
    func switchProvider(_ provider: ChatModelProvider) {
        Task { await switchToProvider(provider) }
    }

    func openCouncilSettings() { activeSheet = .councilSettings }

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

    func startDeepResearch() {
        Task { await switchToProvider(.deep_research) }
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

    func isHorizontalBackSwipe(_ value: DragGesture.Value) -> Bool {
        value.translation.width > 0
            && value.translation.width > abs(value.translation.height) * 1.4
    }

    func isCompletedBackSwipe(_ value: DragGesture.Value) -> Bool {
        isHorizontalBackSwipe(value)
            && value.translation.width > ChatSessionDesign.edgeBackSwipeThreshold
    }

    func snapBackFromEdgeSwipe() {
        withAnimation(AppMotion.press) {
            edgeBackDragOffset = 0
        }
    }

    var activeScreenWidth: CGFloat {
        let windowScene = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first { $0.activationState == .foregroundActive }
            ?? UIApplication.shared.connectedScenes
                .compactMap { $0 as? UIWindowScene }
                .first
        return windowScene?.screen.bounds.width ?? 0
    }
}
