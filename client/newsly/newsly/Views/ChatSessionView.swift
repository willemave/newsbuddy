//
//  ChatSessionView.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import SwiftUI
import UIKit

// MARK: - Share Content

struct ShareContent: Identifiable {
    let id = UUID()
    let messageContent: String
    let articleTitle: String?
    let articleUrl: String?

    var shareText: String {
        var text = messageContent

        if let title = articleTitle {
            text = "**\(title)**\n\n\(text)"
        }

        if let url = articleUrl {
            text += "\n\n\(url)"
        }

        return text
    }
}

struct ShareSheet: UIViewControllerRepresentable {
    let content: ShareContent

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let activityItems: [Any] = [content.shareText]
        let controller = UIActivityViewController(
            activityItems: activityItems,
            applicationActivities: nil
        )
        return controller
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

// MARK: - Selectable Text (UITextView wrapper)

struct SelectableText: UIViewRepresentable {
    let text: String
    let textColor: UIColor
    let font: UIFont
    let maxWidth: CGFloat
    @Binding var calculatedHeight: CGFloat
    var onDigDeeper: ((String) -> Void)?

    init(
        _ text: String,
        textColor: UIColor = .label,
        font: UIFont = .preferredFont(forTextStyle: .callout),
        maxWidth: CGFloat = UIScreen.main.bounds.width,
        calculatedHeight: Binding<CGFloat> = .constant(.zero),
        onDigDeeper: ((String) -> Void)? = nil
    ) {
        self.text = text
        self.textColor = textColor
        self.font = font
        self.maxWidth = maxWidth
        self._calculatedHeight = calculatedHeight
        self.onDigDeeper = onDigDeeper
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onDigDeeper: onDigDeeper)
    }

    func makeUIView(context: Context) -> DigDeeperTextView {
        let textView = DigDeeperTextView()
        textView.isEditable = false
        textView.isSelectable = true
        textView.isScrollEnabled = false
        textView.backgroundColor = .clear
        textView.textContainerInset = .zero
        textView.textContainer.lineFragmentPadding = 0
        textView.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        textView.dataDetectorTypes = [.link]
        textView.onDigDeeper = context.coordinator.onDigDeeper
        return textView
    }

    func updateUIView(_ uiView: DigDeeperTextView, context: Context) {
        uiView.text = text
        uiView.textColor = textColor
        uiView.font = font
        uiView.onDigDeeper = context.coordinator.onDigDeeper
        let fittingSize = uiView.sizeThatFits(CGSize(width: maxWidth, height: .greatestFiniteMagnitude))
        uiView.frame.size = fittingSize
        DispatchQueue.main.async {
            calculatedHeight = fittingSize.height
        }
    }

    class Coordinator {
        var onDigDeeper: ((String) -> Void)?

        init(onDigDeeper: ((String) -> Void)?) {
            self.onDigDeeper = onDigDeeper
        }
    }
}

/// Custom UITextView that adds "Dig Deeper" to the edit menu
class DigDeeperTextView: UITextView {
    var onDigDeeper: ((String) -> Void)?

    override func traitCollectionDidChange(_ previousTraitCollection: UITraitCollection?) {
        guard let previousTraitCollection else {
            super.traitCollectionDidChange(previousTraitCollection)
            return
        }

        let colorAppearanceChanged =
            traitCollection.userInterfaceStyle != previousTraitCollection.userInterfaceStyle
        let sizeCategoryChanged =
            traitCollection.preferredContentSizeCategory != previousTraitCollection.preferredContentSizeCategory
        let layoutDirectionChanged =
            traitCollection.layoutDirection != previousTraitCollection.layoutDirection

        guard colorAppearanceChanged || sizeCategoryChanged || layoutDirectionChanged else {
            return
        }

        super.traitCollectionDidChange(previousTraitCollection)
    }

    override func canPerformAction(_ action: Selector, withSender sender: Any?) -> Bool {
        if action == #selector(digDeeperAction(_:)) {
            return selectedRange.length > 0
        }
        return super.canPerformAction(action, withSender: sender)
    }

    override func buildMenu(with builder: any UIMenuBuilder) {
        super.buildMenu(with: builder)

        let digDeeperAction = UIAction(
            title: "Dig Deeper",
            image: UIImage(systemName: "magnifyingglass")
        ) { [weak self] _ in
            self?.performDigDeeper()
        }

        let menu = UIMenu(title: "", options: .displayInline, children: [digDeeperAction])
        builder.insertChild(menu, atStartOfMenu: .standardEdit)
    }

    @objc func digDeeperAction(_ sender: Any?) {
        performDigDeeper()
    }

    private func performDigDeeper() {
        guard let selectedTextRange = selectedTextRange,
              let selectedText = text(in: selectedTextRange),
              !selectedText.isEmpty else { return }

        let callback = onDigDeeper
        let captured = selectedText

        // Resign first responder to dismiss the edit menu/selection,
        // then dispatch callback to avoid blocking the UIKit run loop.
        resignFirstResponder()
        DispatchQueue.main.async {
            callback?(captured)
        }
    }
}

struct SelectableAttributedText: UIViewRepresentable {
    let attributedText: NSAttributedString
    let textColor: UIColor
    let maxWidth: CGFloat
    @Binding var calculatedHeight: CGFloat
    var onDigDeeper: ((String) -> Void)?

    init(
        attributedText: NSAttributedString,
        textColor: UIColor,
        maxWidth: CGFloat = UIScreen.main.bounds.width,
        calculatedHeight: Binding<CGFloat> = .constant(.zero),
        onDigDeeper: ((String) -> Void)? = nil
    ) {
        self.attributedText = attributedText
        self.textColor = textColor
        self.maxWidth = maxWidth
        self._calculatedHeight = calculatedHeight
        self.onDigDeeper = onDigDeeper
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onDigDeeper: onDigDeeper)
    }

    func makeUIView(context: Context) -> DigDeeperTextView {
        let textView = DigDeeperTextView()
        textView.isEditable = false
        textView.isSelectable = true
        textView.isScrollEnabled = false
        textView.backgroundColor = .clear
        textView.textContainerInset = .zero
        textView.textContainer.lineFragmentPadding = 0
        textView.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        textView.dataDetectorTypes = [.link]
        textView.onDigDeeper = context.coordinator.onDigDeeper
        return textView
    }

    func updateUIView(_ uiView: DigDeeperTextView, context: Context) {
        // Apply the attributed string with color override
        let mutableAttr = NSMutableAttributedString(attributedString: attributedText)
        mutableAttr.addAttribute(.foregroundColor, value: textColor, range: NSRange(location: 0, length: mutableAttr.length))
        uiView.attributedText = mutableAttr
        uiView.onDigDeeper = context.coordinator.onDigDeeper
        let fittingSize = uiView.sizeThatFits(CGSize(width: maxWidth, height: .greatestFiniteMagnitude))
        uiView.frame.size = fittingSize
        DispatchQueue.main.async {
            calculatedHeight = fittingSize.height
        }
    }

    class Coordinator {
        var onDigDeeper: ((String) -> Void)?

        init(onDigDeeper: ((String) -> Void)?) {
            self.onDigDeeper = onDigDeeper
        }
    }
}

struct ChatSessionView: View {
    @StateObject private var viewModel: ChatSessionViewModel
    let onShowHistory: (() -> Void)?
    private let resetsScrollStateOnOpen: Bool
    @FocusState private var isInputFocused: Bool
    @State private var showingModelPicker = false
    @State private var navigateToNewSessionId: Int?
    @State private var shareContent: ShareContent?
    @State private var scrolledMessageId: String?
    @State private var storedScrollState: ChatScrollState?
    @State private var hasRestoredScroll = false
    @State private var isAtBottom = false

    init(
        session: ChatSessionSummary,
        onShowHistory: (() -> Void)? = nil
    ) {
        _viewModel = StateObject(
            wrappedValue: ChatSessionViewModel(session: session)
        )
        self.onShowHistory = onShowHistory
        self.resetsScrollStateOnOpen = false
    }

    init(
        route: ChatSessionRoute,
        onShowHistory: (() -> Void)? = nil
    ) {
        let initialPendingUserMessage: ChatMessage?
        if let text = route.initialUserMessageText,
           !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            initialPendingUserMessage = ChatMessage(
                id: route.pendingMessageId ?? route.sessionId,
                sourceMessageId: route.pendingMessageId,
                role: .user,
                timestamp: route.initialUserMessageTimestamp ?? ISO8601DateFormatter().string(from: Date()),
                content: text,
                status: .processing
            )
        } else {
            initialPendingUserMessage = nil
        }

        _viewModel = StateObject(
            wrappedValue: ChatSessionViewModel(
                sessionId: route.sessionId,
                initialPendingUserMessage: initialPendingUserMessage,
                initialPendingMessageId: route.pendingMessageId,
                pendingCouncilPrompt: route.pendingCouncilPrompt
            )
        )
        self.onShowHistory = onShowHistory
        self.resetsScrollStateOnOpen =
            !(route.pendingCouncilPrompt?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true)
    }

    init(
        sessionId: Int,
        onShowHistory: (() -> Void)? = nil
    ) {
        _viewModel = StateObject(
            wrappedValue: ChatSessionViewModel(sessionId: sessionId)
        )
        self.onShowHistory = onShowHistory
        self.resetsScrollStateOnOpen = false
    }

    private var titleMaxWidth: CGFloat {
        min(UIScreen.main.bounds.width * 0.6, 260)
    }

    private var thinkingIndicatorScrollId: String {
        "__thinking__|\(viewModel.sessionId)"
    }

    private var defaultCouncilPrompt: String {
        if let title = viewModel.session?.articleTitle ?? viewModel.session?.displayTitle,
           !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Give me your perspective on \(title). Keep it short: 2-4 concise bullets on what matters, what is weak or missing, and what follows."
        }
        return "Give me your perspective on this conversation. Keep it short: 2-4 concise bullets on what matters, what is weak or missing, and what follows."
    }

    private func messageScrollId(for message: ChatMessage) -> String {
        "\(viewModel.sessionId)|\(message.id)|\(message.timestamp)|\(message.displayType.rawValue)"
    }

    private func storedMessageId(from scrollId: String?) -> Int? {
        guard let scrollId else { return nil }
        let components = scrollId.components(separatedBy: "|")
        guard components.count >= 2 else { return nil }
        return Int(components[1])
    }

    var body: some View {
        messageListView
            .safeAreaInset(edge: .bottom, spacing: 0) {
                inputBar
            }
            .scrollDismissesKeyboard(.interactively)
        .navigationBarTitleDisplayMode(.inline)
        .task(id: viewModel.sessionId) {
            if resetsScrollStateOnOpen {
                ChatScrollStateStore.clear(sessionId: viewModel.sessionId)
                storedScrollState = nil
                hasRestoredScroll = false
                scrolledMessageId = nil
            }
            await viewModel.loadSession()
            await viewModel.checkAndRefreshVoiceDictation()
        }
        .toolbar {
            if let session = viewModel.session {
                // Session title (tappable if linked to article)
                ToolbarItem(placement: .topBarLeading) {
                    if let articleUrl = session.articleUrl, let url = URL(string: articleUrl) {
                        Button {
                            UIApplication.shared.open(url)
                        } label: {
                            HStack(spacing: 4) {
                                Text(session.displayTitle)
                                    .font(.subheadline)
                                    .fontWeight(.semibold)
                                    .lineLimit(1)
                                    .truncationMode(.tail)
                                Image(systemName: "arrow.up.right.square")
                                    .font(.caption2)
                            }
                            .foregroundColor(Color.onSurface)
                        }
                    } else {
                        Text(session.displayTitle)
                            .font(.subheadline)
                            .fontWeight(.semibold)
                            .lineLimit(1)
                            .truncationMode(.tail)
                    }
                }

                if let onShowHistory {
                    ToolbarItem(placement: .navigationBarTrailing) {
                        Button {
                            onShowHistory()
                        } label: {
                            Image(systemName: "clock.arrow.circlepath")
                        }
                        .accessibilityIdentifier("knowledge.chat_history")
                    }
                }

                if viewModel.canStartCouncil {
                    ToolbarItem(placement: .navigationBarTrailing) {
                        Button {
                            Task {
                                await viewModel.startCouncil(message: defaultCouncilPrompt)
                            }
                        } label: {
                            Group {
                                if viewModel.isStartingCouncil {
                                    ProgressView()
                                        .controlSize(.small)
                                } else {
                                    Image(systemName: "person.3.sequence.fill")
                                }
                            }
                        }
                        .disabled(viewModel.isStartingCouncil)
                        .accessibilityIdentifier("knowledge.start_council")
                    }
                }

                // Provider selector (trailing, icon-only)
                ToolbarItem(placement: .navigationBarTrailing) {
                    Menu {
                        Section {
                            Text("Current: \(session.providerDisplayName)")
                                .font(.caption)
                        }
                        Section("Switch Model") {
                            ForEach(ChatModelProvider.allCases, id: \.self) { provider in
                                Button {
                                    Task {
                                        await switchToProvider(provider)
                                    }
                                } label: {
                                    Label(provider.chatDisplayName, systemImage: provider.iconName)
                                }
                                .disabled(provider.rawValue == session.llmProvider)
                            }
                        }
                    } label: {
                        // Icon-only button (smaller footprint)
                        Group {
                            if let assetName = session.providerIconAsset {
                                Image(assetName)
                                    .resizable()
                                    .aspectRatio(contentMode: .fit)
                                    .frame(width: 22, height: 22)
                            } else {
                                Image(systemName: session.providerIconFallback)
                                    .font(.system(size: 16))
                                    .foregroundColor(Color.onSurfaceSecondary)
                            }
                        }
                        .frame(width: 32, height: 32)
                        .background(Color.secondary.opacity(0.1))
                        .cornerRadius(8)
                    }
                    .disabled(session.isCouncilMode)
                    .opacity(session.isCouncilMode ? 0.45 : 1)
                }
            }
        }
        .navigationDestination(item: $navigateToNewSessionId) { sessionId in
            ChatSessionView(sessionId: sessionId)
        }
        .sheet(item: $shareContent) { content in
            ShareSheet(content: content)
        }
    }

    /// Switch to a different provider without restarting the chat
    private func switchToProvider(_ provider: ChatModelProvider) async {
        guard let currentSession = viewModel.session else { return }

        do {
            let chatService = ChatService.shared
            let updatedSession = try await chatService.updateSessionProvider(
                sessionId: currentSession.id,
                provider: provider
            )

            // Update the local session state to reflect the new provider
            viewModel.updateSession(updatedSession)
        } catch {
            viewModel.errorMessage = "Failed to switch model: \(error.localizedDescription)"
        }
    }

    // MARK: - Message List

    private var messageListView: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if viewModel.isLoading && viewModel.allMessages.isEmpty {
                        ChatLoadingView()
                            .frame(maxWidth: .infinity)
                            .padding(.top, 40)
                    } else if let error = viewModel.errorMessage, viewModel.messages.isEmpty {
                        VStack(spacing: 8) {
                            Image(systemName: "exclamationmark.triangle")
                                .font(.largeTitle)
                                .foregroundColor(Color.statusDestructive)
                            Text(error)
                                .foregroundColor(Color.onSurfaceSecondary)
                                .multilineTextAlignment(.center)
                            Button("Retry") {
                                Task { await viewModel.loadSession() }
                            }
                            .buttonStyle(.borderedProminent)
                        }
                        .padding()
                    } else if viewModel.allMessages.isEmpty {
                        Group {
                            if viewModel.isStartingCouncil {
                                VStack(alignment: .leading, spacing: 18) {
                                    if let session = viewModel.session,
                                       let articleTitle = session.articleTitle {
                                        articlePreviewCard(
                                            title: articleTitle,
                                            source: session.articleSource,
                                            summary: session.articleSummary,
                                            url: session.articleUrl
                                        )
                                    }

                                    ThinkingBubbleView(
                                        elapsedSeconds: viewModel.thinkingElapsedSeconds,
                                        statusText: "Gathering council perspectives"
                                    )
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            } else if viewModel.isSending {
                                InitialSuggestionsLoadingView()
                                    .frame(maxWidth: .infinity)
                            } else if let session = viewModel.session,
                                      let articleTitle = session.articleTitle {
                                // Empty session with article - show article preview
                                articlePreviewCard(
                                    title: articleTitle,
                                    source: session.articleSource,
                                    summary: session.articleSummary,
                                    url: session.articleUrl
                                )
                            } else {
                                VStack(spacing: 16) {
                                    Image(systemName: "bubble.left.and.bubble.right")
                                        .font(.system(size: 48))
                                        .foregroundColor(.secondary.opacity(0.5))
                                    Text("Start the conversation")
                                        .font(.headline)
                                        .foregroundColor(Color.onSurfaceSecondary)
                                    if let topic = viewModel.session?.topic {
                                        Text("Topic: \(topic)")
                                            .font(.subheadline)
                                            .foregroundColor(Color.topicAccent)
                                    }
                                }
                                .frame(maxWidth: .infinity)
                                .multilineTextAlignment(.center)
                            }
                        }
                        .padding(.top, 40)
                    } else {
                        ForEach(viewModel.allMessages, id: \.uiIdentity) { message in
                            MessageBubble(
                                message: message,
                                articleTitle: viewModel.session?.articleTitle,
                                articleUrl: viewModel.session?.articleUrl,
                                selectingCouncilChildSessionId: viewModel.selectingCouncilChildSessionId,
                                onDigDeeper: { selectedText in
                                    Task { await viewModel.digDeeper(into: selectedText) }
                                },
                                onSelectCouncilCandidate: { childSessionId in
                                    Task { await viewModel.selectCouncilBranch(childSessionId: childSessionId) }
                                },
                                onShare: { content in
                                    shareContent = ShareContent(
                                        messageContent: content,
                                        articleTitle: viewModel.session?.articleTitle,
                                        articleUrl: viewModel.session?.articleUrl
                                    )
                                }
                            )
                            .id(messageScrollId(for: message))
                        }

                        if viewModel.isSending {
                            ThinkingBubbleView(
                                elapsedSeconds: viewModel.thinkingElapsedSeconds,
                                statusText: viewModel.latestProcessSummary
                            )
                            .id(thinkingIndicatorScrollId)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .transition(.opacity.combined(with: .move(edge: .bottom)))
                        }
                    }
                }
                .scrollTargetLayout()
                .padding()
            }
            .scrollPosition(id: $scrolledMessageId, anchor: .bottom)
            .onChange(of: scrolledMessageId) { _, newId in
                updateIsAtBottom(anchorId: newId)
                persistScrollPosition(anchorId: newId)
            }
            .onChange(of: viewModel.allMessages.count) { _, _ in
                restoreScrollPositionIfNeeded(proxy: proxy)
                if isAtBottom {
                    scrollToBottom(proxy: proxy, animated: true)
                }
            }
            .onChange(of: viewModel.isSending) { _, isSending in
                if isSending, isAtBottom {
                    scrollToBottom(proxy: proxy, animated: true)
                }
            }
            .onChange(of: viewModel.isLoading) { _, isLoading in
                if !isLoading {
                    restoreScrollPositionIfNeeded(proxy: proxy)
                }
            }
            .onAppear {
                storedScrollState = resetsScrollStateOnOpen
                    ? nil
                    : ChatScrollStateStore.load(sessionId: viewModel.sessionId)
                restoreScrollPositionIfNeeded(proxy: proxy)
            }
            .onDisappear {
                persistScrollPosition(anchorId: scrolledMessageId)
            }
        }
    }

    private func scrollToBottom(proxy: ScrollViewProxy, animated: Bool) {
        let targetId = viewModel.isSending
            ? thinkingIndicatorScrollId
            : viewModel.allMessages.last.map(messageScrollId(for:))
        guard let targetId else { return }
        if animated {
            withAnimation(.easeOut(duration: 0.2)) {
                proxy.scrollTo(targetId, anchor: .bottom)
            }
        } else {
            proxy.scrollTo(targetId, anchor: .bottom)
        }
    }

    private func updateIsAtBottom(anchorId: String?) {
        guard let lastMessage = viewModel.allMessages.last else {
            isAtBottom = false
            return
        }
        let lastId = messageScrollId(for: lastMessage)
        isAtBottom =
            anchorId == lastId ||
            (viewModel.isSending && anchorId == thinkingIndicatorScrollId)
    }

    private func restoreScrollPositionIfNeeded(proxy: ScrollViewProxy) {
        guard !hasRestoredScroll else { return }
        guard !viewModel.allMessages.isEmpty else { return }

        hasRestoredScroll = true
        guard let storedScrollState else {
            scrollToBottom(proxy: proxy, animated: false)
            return
        }

        if storedScrollState.wasAtBottom {
            scrollToBottom(proxy: proxy, animated: false)
            return
        }

        if let anchorId = storedScrollState.anchorMessageId,
           let anchorMessage = viewModel.allMessages.first(where: { $0.id == anchorId }) {
            proxy.scrollTo(messageScrollId(for: anchorMessage), anchor: .bottom)
            return
        }

        if let firstMessage = viewModel.allMessages.first {
            proxy.scrollTo(messageScrollId(for: firstMessage), anchor: .top)
        }
    }

    private func persistScrollPosition(anchorId: String?) {
        guard hasRestoredScroll else { return }
        guard !viewModel.allMessages.isEmpty else { return }
        ChatScrollStateStore.save(
            sessionId: viewModel.sessionId,
            anchorMessageId: storedMessageId(from: anchorId),
            wasAtBottom: isAtBottom
        )
    }

    // MARK: - Article Preview Card (for empty favorites)

    @ViewBuilder
    private func articlePreviewCard(
        title: String,
        source: String?,
        summary: String?,
        url: String?
    ) -> some View {
        VStack(spacing: 16) {
            // Article card
            VStack(alignment: .leading, spacing: 12) {
                Text(title)
                    .font(.headline)
                    .lineLimit(3)

                if let source = source {
                    HStack(spacing: 4) {
                        Image(systemName: "doc.text")
                            .font(.caption)
                        Text(source)
                            .font(.caption)
                    }
                    .foregroundColor(Color.onSurfaceSecondary)
                }

                if let summary = summary, !summary.isEmpty {
                    Text(summary)
                        .font(.subheadline)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .lineLimit(4)
                }

                if let urlString = url, let articleUrl = URL(string: urlString) {
                    Link(destination: articleUrl) {
                        HStack(spacing: 4) {
                            Text("Read original article")
                                .font(.caption)
                            Image(systemName: "arrow.up.right.square")
                                .font(.caption2)
                        }
                        .foregroundColor(Color.topicAccent)
                    }
                }
            }
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.surfaceSecondary)
            .cornerRadius(12)
            .padding(.horizontal)

            // Prompt to start chatting
            VStack(spacing: 8) {
                Text("Ask me anything about this article")
                    .font(.subheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
                Text("I can summarize, explain, find related topics, or answer your questions.")
                    .font(.caption)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
            }
            .padding(.horizontal)
        }
    }

    // MARK: - Input Bar

    private var inputBar: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .center, spacing: 10) {
                HStack(spacing: 8) {
                    TextField("Message", text: $viewModel.inputText, axis: .vertical)
                        .textFieldStyle(.plain)
                        .font(.terracottaBodyMedium)
                        .lineLimit(1...5)
                        .focused($isInputFocused)
                        .accessibilityIdentifier("knowledge.chat_input")
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(Color.surfaceContainerHighest)
                .clipShape(Capsule())
                .overlay(
                    Capsule()
                        .stroke(
                            viewModel.isRecording ? Color.statusDestructive.opacity(0.6) : Color.outlineVariant.opacity(0.3),
                            lineWidth: 1
                        )
                )
                .frame(maxWidth: .infinity)

                TapToTalkMicButton(
                    isEnabled: !viewModel.isSending && !viewModel.isVoiceActionInFlight && !viewModel.isTranscribing,
                    isRecording: viewModel.isRecording,
                    isBusy: viewModel.isVoiceActionInFlight && !viewModel.isRecording,
                    size: 38,
                    action: {
                        Task { await viewModel.toggleVoiceRecording() }
                    }
                )
                .opacity(viewModel.voiceDictationAvailable || viewModel.isRecording ? 1 : 0.72)
                .accessibilityLabel(viewModel.isRecording ? "Stop recording" : "Start recording")
                .accessibilityHint(viewModel.isRecording ? "Tap to stop and transcribe into this chat" : "Tap to dictate into this chat")
                .accessibilityIdentifier("knowledge.chat_mic")

                Button {
                    Task { await viewModel.sendMessage() }
                } label: {
                    Group {
                        if viewModel.isSending {
                            ProgressView()
                                .progressViewStyle(CircularProgressViewStyle(tint: sendButtonDisabled ? .secondary : .white))
                        } else {
                            Image(systemName: "arrow.up")
                                .font(.system(size: 16, weight: .medium))
                        }
                    }
                    .foregroundColor(sendButtonDisabled ? Color.onSurfaceSecondary : .white)
                    .frame(width: 38, height: 38, alignment: .center)
                    .background(sendButtonDisabled ? Color.surfaceContainer : Color.chatUserBubble)
                    .clipShape(Circle())
                }
                .disabled(sendButtonDisabled)
                .accessibilityIdentifier("knowledge.chat_send")
            }

            if viewModel.isTranscribing || viewModel.isRecording {
                VStack(alignment: .leading, spacing: 4) {
                    if viewModel.isTranscribing {
                        HStack(spacing: 4) {
                            ProgressView()
                                .scaleEffect(0.7)
                            Text("Transcribing...")
                                .font(.terracottaBodySmall)
                                .foregroundColor(.onSurfaceSecondary)
                        }
                    }

                    if viewModel.isRecording {
                        HStack(spacing: 6) {
                            Circle()
                                .fill(Color.statusDestructive)
                                .frame(width: 8, height: 8)
                                .scaleEffect(1.0)
                                .overlay(
                                    Circle()
                                        .stroke(Color.statusDestructive.opacity(0.24), lineWidth: 6)
                                        .scaleEffect(1.5)
                                )
                                .font(.terracottaBodySmall)
                            Text("Recording. Tap the mic again to stop.")
                                .font(.terracottaBodySmall)
                                .foregroundColor(.onSurfaceSecondary)
                        }
                    }
                }
                .transition(.opacity.combined(with: .move(edge: .bottom)))
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 14)
        .padding(.bottom, 8)
        .background(
            LinearGradient(
                stops: [
                    .init(color: Color.surfacePrimary.opacity(0), location: 0),
                    .init(color: Color.surfacePrimary, location: 0.15),
                    .init(color: Color.surfacePrimary, location: 1.0),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
        )
    }

    private var sendButtonDisabled: Bool {
        viewModel.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
        viewModel.isSending ||
        viewModel.isRecording ||
        viewModel.isTranscribing
    }
}

// MARK: - Message Bubble

struct MessageBubble: View {
    let message: ChatMessage
    let articleTitle: String?
    let articleUrl: String?
    let selectingCouncilChildSessionId: Int?
    var onDigDeeper: ((String) -> Void)?
    var onSelectCouncilCandidate: ((Int) -> Void)?
    var onShare: ((String) -> Void)?
    @Environment(\.openURL) private var openURL
    @StateObject private var feedOptionActionModel = AssistantFeedOptionActionModel()

    var body: some View {
        Group {
            if message.isProcessSummary {
                ProcessSummaryRow(message: message)
            } else {
                HStack(alignment: .top, spacing: 8) {
                    if message.isUser {
                        Spacer(minLength: 40)
                    } else {
                        // AI identity avatar
                        Circle()
                            .fill(Color.chatAccent)
                            .frame(width: 24, height: 24)
                            .overlay(
                                Image(systemName: "brain.head.profile")
                                    .font(.system(size: 11))
                                    .foregroundStyle(.white)
                            )
                            .padding(.top, 2)
                    }

                    VStack(alignment: message.isUser ? .trailing : .leading, spacing: 4) {
                        if rendersOwnBubble {
                            messageContent
                        } else {
                            messageContent
                                .padding(.horizontal, 14)
                                .padding(.vertical, 10)
                                .background(bubbleBackground)
                                .clipShape(bubbleShape)
                                .overlay(
                                    bubbleShape
                                        .stroke(
                                            message.isUser ? Color.clear : Color.outlineVariant.opacity(0.20),
                                            lineWidth: 0.5
                                        )
                                )
                        }

                        if !message.formattedTime.isEmpty {
                            Text(message.formattedTime)
                                .font(.caption2)
                                .foregroundColor(Color.onSurfaceSecondary)
                                .padding(.horizontal, 4)
                        }
                    }
                    .contextMenu {
                        if message.isAssistant {
                            Button {
                                onShare?(message.content)
                            } label: {
                                Label("Share", systemImage: "square.and.arrow.up")
                            }
                        }

                        Button {
                            UIPasteboard.general.string = message.content
                        } label: {
                            Label("Copy", systemImage: "doc.on.doc")
                        }
                    }

                    if !message.isUser {
                        Spacer(minLength: 20)
                    }
                }
            }
        }
    }

    private var bubbleBackground: Color {
        message.isUser ? Color.chatUserBubble : Color.surfaceContainer
    }

    private var rendersOwnBubble: Bool {
        message.isAssistant && message.hasCouncilCandidates
    }

    private var textColor: UIColor {
        message.isUser ? .white : UIColor(Color.onSurface)
    }

    private var bubbleShape: UnevenRoundedRectangle {
        if message.isUser {
            UnevenRoundedRectangle(topLeadingRadius: 16, bottomLeadingRadius: 16, bottomTrailingRadius: 16, topTrailingRadius: 4)
        } else {
            UnevenRoundedRectangle(topLeadingRadius: 4, bottomLeadingRadius: 16, bottomTrailingRadius: 16, topTrailingRadius: 16)
        }
    }

    private var assistantRenderIdentity: String {
        "\(message.id)|\(message.timestamp)|\(message.displayType.rawValue)|\(message.content)"
    }

    private var messageContent: some View {
        VStack(alignment: .leading, spacing: 12) {
            Group {
                if message.isAssistant && message.hasCouncilCandidates {
                    CouncilCandidatesBubble(
                        message: message,
                        textColor: textColor,
                        selectingChildSessionId: selectingCouncilChildSessionId,
                        onSelectCouncilCandidate: onSelectCouncilCandidate
                    )
                } else if message.isUser {
                    Text(message.content)
                        .font(.callout)
                        .foregroundColor(Color(textColor))
                        .textSelection(.enabled)
                } else {
                    SelectableMarkdownView(
                        markdown: message.content,
                        textColor: textColor,
                        baseFont: .preferredFont(forTextStyle: .callout),
                        onDigDeeper: onDigDeeper
                    )
                    .id(assistantRenderIdentity)
                }
            }
            if message.isAssistant && message.hasFeedOptions {
                AssistantFeedOptionsSection(
                    options: message.feedOptions,
                    actionModel: feedOptionActionModel,
                    onPreview: { option in
                        guard let url = URL(string: option.previewURLString) else { return }
                        openURL(url)
                    }
                )
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: message.isUser ? nil : .infinity, alignment: message.isUser ? .trailing : .leading)
    }
}

private struct CouncilCandidatesBubble: View {
    let message: ChatMessage
    let textColor: UIColor
    let selectingChildSessionId: Int?
    let onSelectCouncilCandidate: ((Int) -> Void)?

    @State private var selectedChildSessionId: Int?

    private var candidates: [CouncilCandidate] {
        message.councilCandidates.sorted { $0.order < $1.order }
    }

    private var selectedCandidate: CouncilCandidate? {
        if let selectedChildSessionId,
           let candidate = candidates.first(where: { $0.childSessionId == selectedChildSessionId }) {
            return candidate
        }

        return candidates.first
    }

    private var activeChildSessionId: Int? {
        message.activeCouncilChildSessionId ?? candidates.first?.childSessionId
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ScrollView(.horizontal) {
                HStack(spacing: 8) {
                    ForEach(candidates) { candidate in
                        CouncilCandidateTab(
                            candidate: candidate,
                            isSelected: selectedChildSessionId == candidate.childSessionId,
                            isActive: activeChildSessionId == candidate.childSessionId,
                            isSelecting: selectingChildSessionId == candidate.childSessionId,
                            isInteractionDisabled: selectingChildSessionId != nil
                        ) {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                selectedChildSessionId = candidate.childSessionId
                            }
                            guard activeChildSessionId != candidate.childSessionId else { return }
                            onSelectCouncilCandidate?(candidate.childSessionId)
                        }
                    }
                }
                .padding(.vertical, 2)
            }
            .scrollIndicators(.hidden)

            if let selectedCandidate {
                CouncilCandidateCard(
                    candidate: selectedCandidate,
                    textColor: textColor,
                    isSelected: true,
                    isActive: activeChildSessionId == selectedCandidate.childSessionId,
                    isSelecting: selectingChildSessionId == selectedCandidate.childSessionId
                )
                .transition(.opacity.combined(with: .move(edge: .bottom)))
            }
        }
        .onAppear {
            selectedChildSessionId = activeChildSessionId
        }
        .onChange(of: message.activeCouncilChildSessionId) { _, newValue in
            if let newValue {
                selectedChildSessionId = newValue
                return
            }

            selectedChildSessionId = candidates.first?.childSessionId
        }
        .onChange(of: selectingChildSessionId) { _, newValue in
            guard newValue == nil else { return }
            guard selectedChildSessionId != activeChildSessionId else { return }
            selectedChildSessionId = activeChildSessionId
        }
        .animation(.easeInOut(duration: 0.2), value: selectedChildSessionId)
    }
}

private struct CouncilCandidateCard: View {
    let candidate: CouncilCandidate
    let textColor: UIColor
    let isSelected: Bool
    let isActive: Bool
    let isSelecting: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(candidate.personaName)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Color.onSurface)

                    if isActive {
                        Text("Current branch")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(Color.chatAccent)
                    } else if isSelecting {
                        Text("Switching branch")
                            .font(.caption2.weight(.medium))
                            .foregroundStyle(Color.chatAccent)
                    } else {
                        Text("Select a tab to switch")
                            .font(.caption2)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                }

                Spacer(minLength: 0)

                if isSelecting {
                    ProgressView()
                        .controlSize(.small)
                } else if isActive {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(Color.chatAccent)
                }
            }

            SelectableMarkdownView(
                markdown: candidate.content,
                textColor: textColor,
                baseFont: .preferredFont(forTextStyle: .callout)
            )
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(
            UnevenRoundedRectangle(
                topLeadingRadius: 4,
                bottomLeadingRadius: 16,
                bottomTrailingRadius: 16,
                topTrailingRadius: 16
            )
            .fill(isSelected ? Color.surfaceContainer : Color.surfaceSecondary.opacity(0.92))
        )
        .overlay(
            UnevenRoundedRectangle(
                topLeadingRadius: 4,
                bottomLeadingRadius: 16,
                bottomTrailingRadius: 16,
                topTrailingRadius: 16
            )
            .stroke(
                isSelected ? Color.chatAccent.opacity(0.35) : Color.outlineVariant.opacity(0.18),
                lineWidth: 1
            )
        )
    }
}

private struct CouncilCandidateTab: View {
    let candidate: CouncilCandidate
    let isSelected: Bool
    let isActive: Bool
    let isSelecting: Bool
    let isInteractionDisabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(candidate.personaName)
                    .lineLimit(1)

                if isSelecting {
                    ProgressView()
                        .controlSize(.small)
                } else if isActive {
                    Image(systemName: "checkmark.circle.fill")
                }
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(isSelected ? Color.chatAccent : Color.onSurfaceSecondary)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(
                Capsule()
                    .fill(isSelected ? Color.chatAccent.opacity(0.14) : Color.surfaceSecondary.opacity(0.72))
            )
            .overlay(
                Capsule()
                    .stroke(
                        isSelected ? Color.chatAccent.opacity(0.32) : Color.outlineVariant.opacity(0.18),
                        lineWidth: 1
                    )
            )
        }
        .buttonStyle(.plain)
        .disabled(isInteractionDisabled)
        .accessibilityIdentifier("council.tab.\(candidate.childSessionId)")
        .accessibilityLabel(isActive ? "\(candidate.personaName), current branch" : candidate.personaName)
        .accessibilityHint("Switches the active council branch")
    }
}

struct ProcessSummaryRow: View {
    let message: ChatMessage

    var body: some View {
        HStack {
            Spacer(minLength: 0)
            HStack(spacing: 6) {
                Image(systemName: "sparkles")
                    .font(.caption2)
                Text(message.processSummaryText)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .font(.terracottaBodySmall)
            .foregroundColor(Color.onSurfaceSecondary)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Color.surfaceContainer.opacity(0.8))
            .clipShape(Capsule())
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity)
        .accessibilityLabel(message.processSummaryText)
    }
}

@MainActor
protocol AssistantFeedSubscribing: AnyObject {
    func subscribeFeed(
        feedURL: String,
        feedType: String,
        displayName: String?
    ) async throws -> ScraperConfig
}

extension ScraperConfigService: AssistantFeedSubscribing {}

@MainActor
final class AssistantFeedOptionActionModel: ObservableObject {
    @Published private(set) var subscribedOptionIds: Set<String> = []
    @Published private(set) var subscribingOptionIds: Set<String> = []

    private let service: any AssistantFeedSubscribing

    init(service: any AssistantFeedSubscribing = ScraperConfigService.shared) {
        self.service = service
    }

    func isSubscribed(_ option: AssistantFeedOption) -> Bool {
        subscribedOptionIds.contains(option.id)
    }

    func isSubscribing(_ option: AssistantFeedOption) -> Bool {
        subscribingOptionIds.contains(option.id)
    }

    func subscribe(_ option: AssistantFeedOption) async {
        guard !isSubscribed(option), !isSubscribing(option) else { return }

        subscribingOptionIds.insert(option.id)
        defer { subscribingOptionIds.remove(option.id) }

        do {
            _ = try await service.subscribeFeed(
                feedURL: option.feedURL,
                feedType: option.feedType,
                displayName: option.title
            )
            subscribedOptionIds.insert(option.id)
            ToastService.shared.showSuccess("Subscribed to \(option.title)")
        } catch let apiError as APIError {
            if case .httpError(let statusCode) = apiError, statusCode == 400 {
                subscribedOptionIds.insert(option.id)
                ToastService.shared.show("Already subscribed", type: .info)
                return
            }
            ToastService.shared.showError("Failed to subscribe: \(apiError.localizedDescription)")
        } catch {
            ToastService.shared.showError("Failed to subscribe: \(error.localizedDescription)")
        }
    }
}

struct AssistantFeedOptionsSection: View {
    let options: [AssistantFeedOption]
    @ObservedObject var actionModel: AssistantFeedOptionActionModel
    let onPreview: (AssistantFeedOption) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(options) { option in
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        Image(systemName: option.systemIcon)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(Color.terracottaPrimary)
                        Text(option.feedTypeLabel.uppercased())
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.secondary)
                        Text("·")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                        Text(option.hostLabel)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }

                    Text(option.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.primary)
                        .fixedSize(horizontal: false, vertical: true)

                    if let subtitle = option.subtitleText {
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    HStack(spacing: 10) {
                        Button {
                            Task { await actionModel.subscribe(option) }
                        } label: {
                            if actionModel.isSubscribing(option) {
                                ProgressView()
                                    .controlSize(.small)
                            } else {
                                Image(systemName: actionModel.isSubscribed(option) ? "checkmark.circle.fill" : "plus.circle.fill")
                                    .font(.system(size: 20))
                            }
                        }
                        .foregroundStyle(actionModel.isSubscribed(option) ? Color.onSurfaceSecondary : Color.chatUserBubble)
                        .disabled(actionModel.isSubscribed(option) || actionModel.isSubscribing(option))

                        Button {
                            onPreview(option)
                        } label: {
                            Image(systemName: "safari")
                                .font(.system(size: 20))
                        }
                        .foregroundStyle(Color.onSurfaceSecondary)

                        Spacer()
                    }
                }
                .padding(12)
                .background(Color.surfaceTertiary)
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(Color.outlineVariant.opacity(0.5), lineWidth: 0.5)
                )
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
    }
}

// MARK: - Thinking Indicator

struct ThinkingBubbleView: View {
    let elapsedSeconds: Int
    let statusText: String?
    @State private var isAnimating = false

    private var formattedDuration: String {
        String(format: "%02d:%02d", elapsedSeconds / 60, elapsedSeconds % 60)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Circle()
                .fill(Color.chatAccent)
                .frame(width: 24, height: 24)
                .overlay(
                    Image(systemName: "brain.head.profile")
                        .font(.system(size: 11))
                        .foregroundStyle(.white)
                )
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    ForEach(0..<3) { index in
                        Circle()
                            .fill(Color.chatAccent.opacity(0.5))
                            .frame(width: 6, height: 6)
                            .offset(y: isAnimating ? -2 : 2)
                            .animation(
                                .easeInOut(duration: 0.4)
                                    .repeatForever(autoreverses: true)
                                    .delay(Double(index) * 0.1),
                                value: isAnimating
                            )
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(Color.surfaceContainer)
                .clipShape(UnevenRoundedRectangle(topLeadingRadius: 4, bottomLeadingRadius: 16, bottomTrailingRadius: 16, topTrailingRadius: 16))

                if let statusText, !statusText.isEmpty {
                    Text(statusText)
                        .font(.caption)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 4)
                }

                Text(formattedDuration)
                    .font(.caption2)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .monospacedDigit()
                    .padding(.horizontal, 4)
            }
        }
        .onAppear {
            isAnimating = true
        }
    }
}

// MARK: - Initial Suggestions Loading View

struct InitialSuggestionsLoadingView: View {
    @State private var dotOffset: CGFloat = 0
    @State private var pulseScale: CGFloat = 1.0

    var body: some View {
        VStack(spacing: 20) {
            // Animated typing indicator style
            ZStack {
                // Background circle with pulse
                Circle()
                    .fill(Color.chatAccent.opacity(0.08))
                    .frame(width: 80, height: 80)
                    .scaleEffect(pulseScale)

                // Three bouncing dots
                HStack(spacing: 6) {
                    ForEach(0..<3) { index in
                        Circle()
                            .fill(Color.chatAccent.opacity(0.7))
                            .frame(width: 10, height: 10)
                            .offset(y: dotOffset)
                            .animation(
                                .easeInOut(duration: 0.4)
                                    .repeatForever(autoreverses: true)
                                    .delay(Double(index) * 0.12),
                                value: dotOffset
                            )
                    }
                }
            }
            .onAppear {
                dotOffset = -6
                withAnimation(.easeInOut(duration: 1.5).repeatForever(autoreverses: true)) {
                    pulseScale = 1.15
                }
            }

            VStack(spacing: 6) {
                Text("Preparing suggestions")
                    .font(.headline)
                    .foregroundColor(Color.onSurface)

                Text("Analyzing the article for you")
                    .font(.subheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
            }
        }
    }
}

#Preview("Loading State") {
    InitialSuggestionsLoadingView()
}

#Preview {
    NavigationStack {
        ChatSessionView(session: ChatSessionSummary(
            id: 1,
            contentId: nil,
            title: "Test Chat",
            sessionType: "ad_hoc",
            topic: nil,
            llmProvider: "openai",
            llmModel: "openai:gpt-5.4",
            createdAt: "2025-11-28T12:00:00Z",
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: nil,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: false,
            isFavorite: false,
            hasMessages: true,
            lastMessagePreview: nil,
            lastMessageRole: nil
        ))
    }
}
