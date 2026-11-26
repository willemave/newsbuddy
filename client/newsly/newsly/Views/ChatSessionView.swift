import SwiftUI
import UIKit

private enum ChatSessionDesign {
    static let edgeBackSwipeWidth: CGFloat = 28
    static let edgeBackSwipeThreshold: CGFloat = 80
}

struct ChatSessionView: View {
    @Environment(AppLifecycle.self) private var lifecycle
    @Environment(ActiveChatSessionManager.self) private var activeSessionManager
    @Environment(RootDependencyFactory.self) private var dependencyFactory

    private let route: ChatSessionRoute
    private let onShowHistory: (() -> Void)?
    private let onClose: (() -> Void)?
    private let attachment: (AttachedChatController.Presentation, AttachedChatController)?

    @MainActor
    init(
        route: ChatSessionRoute,
        onShowHistory: (() -> Void)? = nil,
        onClose: (() -> Void)? = nil
    ) {
        self.route = route
        self.onShowHistory = onShowHistory
        self.onClose = onClose
        self.attachment = nil
    }

    /// A chat attached to its source. The controller owns the view model, so
    /// this view can be torn down and re-created in another host (a sheet or
    /// an open-display page) without ending the chat.
    @MainActor
    init(
        attached presentation: AttachedChatController.Presentation,
        controller: AttachedChatController
    ) {
        self.route = presentation.route
        self.onShowHistory = nil
        self.onClose = { controller.close() }
        self.attachment = (presentation, controller)
    }

    var body: some View {
        let dependencies = dependencyFactory.makeChatDependencies(
            activeSessionManager: activeSessionManager
        )
        if let (presentation, controller) = attachment {
            ChatSessionContent(
                route: route,
                lifecycle: lifecycle,
                dependencies: dependencies,
                feedOptionActionModel: controller.feedOptionActionModel {
                    dependencyFactory.makeAssistantFeedOptionActionModel()
                },
                attachedViewModel: controller.viewModel(for: presentation) {
                    ChatSessionViewModel(
                        lifecycle: lifecycle,
                        route: route,
                        dependencies: dependencies
                    )
                },
                onClose: onClose
            )
            .id(presentation.id)
        } else {
            ChatSessionContent(
                route: route,
                lifecycle: lifecycle,
                dependencies: dependencies,
                feedOptionActionModel: dependencyFactory.makeAssistantFeedOptionActionModel(),
                onShowHistory: onShowHistory,
                onClose: onClose
            )
        }
    }
}

private struct ChatSessionContent: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.persistentBottomChromeInset) private var persistentBottomChromeInset

    private let lifecycle: AppLifecycle
    private let route: ChatSessionRoute
    private let dependencies: ChatDependencies
    private let onShowHistory: (() -> Void)?
    private let onClose: (() -> Void)?
    /// Attached chats are owned by `AttachedChatController`: this view never
    /// tears the view model down, and it has no edge-back swipe because it is
    /// a sheet or pane rather than a pushed route.
    private let isAttached: Bool

    @State private var viewModel: ChatSessionViewModel
    @State private var feedOptionActionModel: AssistantFeedOptionActionModel
    @FocusState private var isInputFocused: Bool
    @State private var scrollToBottomRequest = 0
    @State private var edgeBackDragOffset: CGFloat = 0
    @State private var edgeBackSwipeFeedbackTrigger = 0
    /// The chat's own width, so the edge-back swipe slides exactly off its
    /// container (a column or the full screen) rather than the device screen.
    @State private var containerWidth: CGFloat = 0

    @MainActor
    init(
        route: ChatSessionRoute,
        lifecycle: AppLifecycle,
        dependencies: ChatDependencies,
        feedOptionActionModel: AssistantFeedOptionActionModel,
        attachedViewModel: ChatSessionViewModel? = nil,
        onShowHistory: (() -> Void)? = nil,
        onClose: (() -> Void)? = nil
    ) {
        self.route = route
        self.lifecycle = lifecycle
        self.dependencies = dependencies
        self.isAttached = attachedViewModel != nil
        _feedOptionActionModel = State(initialValue: feedOptionActionModel)
        _viewModel = State(
            initialValue: attachedViewModel ?? ChatSessionViewModel(
                lifecycle: lifecycle,
                route: route,
                dependencies: dependencies
            )
        )
        self.onShowHistory = onShowHistory
        self.onClose = onClose
    }

    var body: some View {
        // Header and composer are bars rather than stacked rows, so messages
        // scroll beneath both and get the system soft scroll-edge effect.
        messageListView
            .overlay(alignment: .leading) {
                if !isAttached {
                    edgeBackSwipeZone
                }
            }
            .safeAreaBar(edge: .top, spacing: 0) {
                chatHeader
            }
            .safeAreaBar(edge: .bottom, spacing: 0) {
                bottomDock
            }
            .offset(x: edgeBackDragOffset)
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width in
                containerWidth = width
            }
            .scrollDismissesKeyboard(.interactively)
            .extendsToOpenDisplayEdge()
            .navigationBarBackButtonHidden(true)
            .toolbar(.hidden, for: .navigationBar)
            .toolbar(.hidden, for: .tabBar)
            .task(id: route.stableKey) {
                viewModel.handleAppear()
                dependencies.activeSessionManager.stopTracking(sessionId: viewModel.sessionId)
                // A re-hosted attached chat keeps its transcript and any
                // in-flight turn; reloading would race the live send.
                if isAttached, viewModel.hasCompletedInitialLoad {
                    viewModel.resumeAttachedPollingIfNeeded()
                    return
                }
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
                guard !isAttached else { return }
                viewModel.handleDisappear()
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
            isSending: viewModel.isSending,
            thinkingStartedAt: viewModel.thinkingStartedAt,
            latestProcessSummary: viewModel.latestProcessSummary,
            hasVisiblePartialResponse: viewModel.hasVisiblePartialResponse,
            session: viewModel.session,
            scrollToBottomRequest: scrollToBottomRequest,
            feedOptionActionModel: feedOptionActionModel,
            onDismissError: dismissError,
            onRetryLoad: retryLoad,
            onRetrySend: retrySend,
            onDigDeeper: digDeeper,
            onShare: presentShareSheet
        )
    }

    private var chatHeader: some View {
        HStack(spacing: 12) {
            if isAttached {
                // Leading, like the back button it replaces: on the open
                // display the trailing corner sits under the floating clock.
                attachedCloseButton
            } else {
                FloatingBackButton(style: .surface) {
                    closeChat()
                }
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
        .accessibilityElement(children: .contain)
    }

    private var attachedCloseButton: some View {
        Button(action: closeChat) {
            Image(systemName: "xmark")
                .font(.appSymbol(size: 15, weight: .semibold))
                .foregroundStyle(Color.onSurface)
                .frame(width: DetailDesign.floatingBackButtonSize, height: DetailDesign.floatingBackButtonSize)
                .glassEffect(.regular.interactive(), in: .circle)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Close chat")
        .accessibilityIdentifier("chat.attached.close")
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
                withAnimation(AppMotion.subtle) {
                    edgeBackDragOffset = max(containerWidth, value.translation.width)
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
        let shareContent = ShareContent(
            messageContent: content,
            articleTitle: viewModel.session?.articleTitle,
            articleUrl: viewModel.session?.articleUrl
        )
        ActivityViewPresenter.presentWhenReady(UIActivityViewController(
            activityItems: [shareContent.shareText],
            applicationActivities: nil
        ))
    }

    private var bottomDock: some View {
        composerDock
        .padding(.top, 6)
        .padding(.bottom, 6)
        .padding(.bottom, persistentBottomChromeInset)
    }

    private var composerDock: some View {
        ChatComposerDock(
            inputText: $viewModel.inputText,
            isInputFocused: $isInputFocused,
            session: viewModel.session,
            isSending: viewModel.isSending,
            isRecording: viewModel.isRecording,
            isTranscribing: viewModel.isTranscribing,
            isVoiceActionInFlight: viewModel.isVoiceActionInFlight,
            voiceDictationAvailable: viewModel.voiceDictationAvailable,
            onShowHistory: onShowHistory,
            onSwitchProvider: switchProvider,
            onToggleVoiceRecording: toggleVoiceRecording,
            onSend: sendMessage
        )
    }

}

private extension ChatSessionContent {
    func switchProvider(_ provider: ChatModelProvider) {
        Task { await switchToProvider(provider) }
    }

    func dismissError() { viewModel.errorMessage = nil }

    func retryLoad() { Task { await viewModel.loadSession() } }

    func retrySend(_ text: String) {
        scrollToBottomRequest += 1
        viewModel.performSendMessage(text: text)
    }

    func digDeeper(into selectedText: String) {
        viewModel.performDigDeeper(into: selectedText)
    }

    func toggleVoiceRecording() { viewModel.performToggleVoiceRecording() }

    func sendMessage() {
        scrollToBottomRequest += 1
        viewModel.performSendMessage()
    }

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
}
