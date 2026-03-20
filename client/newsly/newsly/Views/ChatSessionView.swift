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
    private static let thinkingIndicatorScrollId = -1
    @StateObject private var viewModel: ChatSessionViewModel
    let onShowHistory: (() -> Void)?
    @FocusState private var isInputFocused: Bool
    @State private var showingModelPicker = false
    @State private var navigateToNewSessionId: Int?
    @State private var shareContent: ShareContent?
    @State private var scrolledMessageId: Int?
    @State private var storedScrollState: ChatScrollState?
    @State private var hasRestoredScroll = false
    @State private var isAtBottom = false
    @Namespace private var holdToTalkNamespace

    init(
        session: ChatSessionSummary,
        onShowHistory: (() -> Void)? = nil
    ) {
        _viewModel = StateObject(
            wrappedValue: ChatSessionViewModel(session: session)
        )
        self.onShowHistory = onShowHistory
    }

    init(
        sessionId: Int,
        onShowHistory: (() -> Void)? = nil
    ) {
        _viewModel = StateObject(
            wrappedValue: ChatSessionViewModel(sessionId: sessionId)
        )
        self.onShowHistory = onShowHistory
    }

    private var titleMaxWidth: CGFloat {
        min(UIScreen.main.bounds.width * 0.6, 260)
    }

    var body: some View {
        messageListView
            .safeAreaInset(edge: .bottom, spacing: 0) {
                inputBar
            }
            .scrollDismissesKeyboard(.interactively)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await viewModel.loadSession()
            await viewModel.checkAndRefreshVoiceDictation()
        }
        .toolbar {
            if let session = viewModel.session {
                // Session title (tappable if linked to article)
                ToolbarItem(placement: .principal) {
                    VStack(spacing: 2) {
                        if let articleUrl = session.articleUrl, let url = URL(string: articleUrl) {
                            // Tappable title that opens the article
                            Button {
                                UIApplication.shared.open(url)
                            } label: {
                                HStack(spacing: 4) {
                                    Text(session.displayTitle)
                                        .font(.headline)
                                        .lineLimit(1)
                                        .truncationMode(.tail)
                                        .layoutPriority(1)
                                    Image(systemName: "arrow.up.right.square")
                                        .font(.caption2)
                                }
                                .frame(maxWidth: titleMaxWidth)
                                .foregroundColor(.primary)
                            }
                        } else {
                            Text(session.displayTitle)
                                .font(.headline)
                                .lineLimit(1)
                                .truncationMode(.tail)
                                .frame(maxWidth: titleMaxWidth)
                        }

                        if session.sessionType != "article_brain" {
                            HStack(spacing: 4) {
                                Image(systemName: session.sessionTypeIconName)
                                    .font(.caption2)
                                Text(session.sessionTypeLabel)
                                    .font(.caption2)
                                    .lineLimit(1)
                                    .truncationMode(.tail)
                                    .layoutPriority(1)
                            }
                            .frame(maxWidth: titleMaxWidth)
                            .foregroundColor(session.isDeepResearch ? .purple : .secondary)
                        }
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
                                    .foregroundColor(.secondary)
                            }
                        }
                        .frame(width: 32, height: 32)
                        .background(Color.secondary.opacity(0.1))
                        .cornerRadius(8)
                    }
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
                    if viewModel.isLoading {
                        ChatLoadingView()
                            .frame(maxWidth: .infinity)
                            .padding(.top, 40)
                    } else if let error = viewModel.errorMessage, viewModel.messages.isEmpty {
                        VStack(spacing: 8) {
                            Image(systemName: "exclamationmark.triangle")
                                .font(.largeTitle)
                                .foregroundColor(.orange)
                            Text(error)
                                .foregroundColor(.secondary)
                                .multilineTextAlignment(.center)
                            Button("Retry") {
                                Task { await viewModel.loadSession() }
                            }
                            .buttonStyle(.borderedProminent)
                        }
                        .padding()
                    } else if viewModel.allMessages.isEmpty {
                        VStack(spacing: 16) {
                            if viewModel.isSending {
                                // Loading initial suggestions
                                InitialSuggestionsLoadingView()
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
                                Image(systemName: "bubble.left.and.bubble.right")
                                    .font(.system(size: 48))
                                    .foregroundColor(.secondary.opacity(0.5))
                                Text("Start the conversation")
                                    .font(.headline)
                                    .foregroundColor(.secondary)
                            }
                            if let topic = viewModel.session?.topic {
                                Text("Topic: \(topic)")
                                    .font(.subheadline)
                                    .foregroundColor(.blue)
                            }
                        }
                        .padding(.top, 40)
                    } else {
                        ForEach(viewModel.allMessages) { message in
                            MessageBubble(
                                message: message,
                                articleTitle: viewModel.session?.articleTitle,
                                articleUrl: viewModel.session?.articleUrl,
                                onDigDeeper: { selectedText in
                                    Task { await viewModel.digDeeper(into: selectedText) }
                                },
                                onShare: { content in
                                    shareContent = ShareContent(
                                        messageContent: content,
                                        articleTitle: viewModel.session?.articleTitle,
                                        articleUrl: viewModel.session?.articleUrl
                                    )
                                }
                            )
                            .id(message.id)
                        }

                        if viewModel.isSending {
                            ThinkingBubbleView(
                                elapsedSeconds: viewModel.thinkingElapsedSeconds
                            )
                            .id(Self.thinkingIndicatorScrollId)
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
                storedScrollState = ChatScrollStateStore.load(sessionId: viewModel.sessionId)
                restoreScrollPositionIfNeeded(proxy: proxy)
            }
            .onDisappear {
                persistScrollPosition(anchorId: scrolledMessageId)
            }
        }
    }

    private func scrollToBottom(proxy: ScrollViewProxy, animated: Bool) {
        let targetId = viewModel.isSending
            ? Self.thinkingIndicatorScrollId
            : viewModel.allMessages.last?.id
        guard let targetId else { return }
        if animated {
            withAnimation(.easeOut(duration: 0.2)) {
                proxy.scrollTo(targetId, anchor: .bottom)
            }
        } else {
            proxy.scrollTo(targetId, anchor: .bottom)
        }
    }

    private func updateIsAtBottom(anchorId: Int?) {
        guard let lastId = viewModel.allMessages.last?.id else {
            isAtBottom = false
            return
        }
        isAtBottom =
            anchorId == lastId ||
            (viewModel.isSending && anchorId == Self.thinkingIndicatorScrollId)
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
           viewModel.allMessages.contains(where: { $0.id == anchorId }) {
            proxy.scrollTo(anchorId, anchor: .bottom)
            return
        }

        if let firstId = viewModel.allMessages.first?.id {
            proxy.scrollTo(firstId, anchor: .top)
        }
    }

    private func persistScrollPosition(anchorId: Int?) {
        guard hasRestoredScroll else { return }
        guard !viewModel.allMessages.isEmpty else { return }
        ChatScrollStateStore.save(
            sessionId: viewModel.sessionId,
            anchorMessageId: anchorId,
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
                    .foregroundColor(.secondary)
                }

                if let summary = summary, !summary.isEmpty {
                    Text(summary)
                        .font(.subheadline)
                        .foregroundColor(.secondary)
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
                        .foregroundColor(.blue)
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
                    .foregroundColor(.secondary)
                Text("I can summarize, explain, find related topics, or answer your questions.")
                    .font(.caption)
                    .foregroundColor(.secondary)
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
                        .lineLimit(1...5)
                        .focused($isInputFocused)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(Color.surfaceSecondary)
                .overlay(
                    RoundedRectangle(cornerRadius: 20)
                        .stroke(
                            viewModel.isRecording ? Color.red.opacity(0.6) : Color(.separator),
                            lineWidth: 1
                        )
                )
                .cornerRadius(20)
                .frame(maxWidth: .infinity)

                HoldToTalkMicButton(
                    isEnabled: !viewModel.isSending,
                    isRecording: viewModel.isRecording,
                    size: 38,
                    namespace: holdToTalkNamespace,
                    matchedId: "chat-session-mic",
                    onPressStart: {
                        Task { await viewModel.startVoiceRecording() }
                    },
                    onPressEnd: {
                        Task { await viewModel.stopVoiceRecording() }
                    }
                )
                .opacity(viewModel.voiceDictationAvailable || viewModel.isRecording ? 1 : 0.72)
                .accessibilityLabel("Hold to talk")
                .accessibilityHint("Press and hold to dictate into this chat")

                Button {
                    Task { await viewModel.sendMessage() }
                } label: {
                    Group {
                        if viewModel.isSending {
                            ProgressView()
                                .progressViewStyle(CircularProgressViewStyle(tint: sendButtonDisabled ? .secondary : .accentColor))
                        } else {
                            Image(systemName: "arrow.up")
                                .font(.system(size: 16, weight: .medium))
                        }
                    }
                    .foregroundColor(sendButtonDisabled ? .secondary : .accentColor)
                    .frame(width: 34, height: 34, alignment: .center)
                    .background(sendButtonDisabled ? Color.clear : Color.accentColor.opacity(0.1))
                    .clipShape(Circle())
                }
                .disabled(sendButtonDisabled)
            }

            if viewModel.isTranscribing || viewModel.isRecording || !viewModel.activeTranscript.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    if viewModel.isTranscribing {
                        HStack(spacing: 4) {
                            ProgressView()
                                .scaleEffect(0.7)
                            Text("Transcribing...")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }

                    if viewModel.isRecording {
                        HStack(spacing: 6) {
                            Image(systemName: "waveform")
                                .font(.caption)
                                .foregroundColor(.red)
                            Text("Listening...")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }

                    if !viewModel.activeTranscript.isEmpty {
                        Text(viewModel.activeTranscript)
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .lineLimit(2)
                    }
                }
                .transition(.opacity)
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .padding(.bottom, 92)
        .background(Color.surfaceSecondary)
        .overlay(
            Rectangle()
                .frame(height: 0.33)
                .foregroundColor(Color(.separator)),
            alignment: .top
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
    var onDigDeeper: ((String) -> Void)?
    var onShare: ((String) -> Void)?
    @Environment(\.openURL) private var openURL
    @StateObject private var feedOptionActionModel = AssistantFeedOptionActionModel()

    var body: some View {
        Group {
            if message.isProcessSummary {
                ProcessSummaryRow(message: message)
            } else {
                HStack {
                    if message.isUser {
                        Spacer(minLength: 60)
                    }

                    VStack(alignment: message.isUser ? .trailing : .leading, spacing: 4) {
                        messageContent
                            .padding(.horizontal, 14)
                            .padding(.vertical, 10)
                            .background(bubbleBackground)
                            .cornerRadius(16)
                            .overlay(
                                RoundedRectangle(cornerRadius: 16)
                                    .stroke(
                                        message.isUser ? Color.clear : Color(.separator),
                                        lineWidth: 0.5
                                    )
                            )

                        if !message.formattedTime.isEmpty {
                            Text(message.formattedTime)
                                .font(.caption2)
                                .foregroundColor(Color(.tertiaryLabel))
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
                }
            }
        }
    }

    private var bubbleBackground: Color {
        message.isUser ? Color.accentColor : Color.surfaceSecondary
    }

    private var textColor: UIColor {
        message.isUser ? .white : .label
    }

    private var messageContent: some View {
        VStack(alignment: .leading, spacing: 12) {
            Group {
                if message.isUser {
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
        .frame(maxWidth: .infinity, alignment: message.isUser ? .trailing : .leading)
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
            .font(.caption)
            .foregroundColor(Color(.secondaryLabel))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Color.surfaceSecondary.opacity(0.8))
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
                            .foregroundStyle(Color.accentColor)
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

                    HStack(spacing: 8) {
                        Button {
                            Task { await actionModel.subscribe(option) }
                        } label: {
                            if actionModel.isSubscribing(option) {
                                ProgressView()
                                    .controlSize(.small)
                                    .frame(maxWidth: .infinity)
                            } else {
                                Label(
                                    actionModel.isSubscribed(option) ? "Subscribed" : "Subscribe",
                                    systemImage: actionModel.isSubscribed(option) ? "checkmark.circle.fill" : "plus"
                                )
                                .frame(maxWidth: .infinity)
                            }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(actionModel.isSubscribed(option) || actionModel.isSubscribing(option))

                        Button {
                            onPreview(option)
                        } label: {
                            Label("Preview", systemImage: "safari")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                    }
                }
                .padding(12)
                .background(Color.black.opacity(0.03))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(Color(.separator).opacity(0.5), lineWidth: 0.5)
                )
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
    }
}

// MARK: - Thinking Indicator

struct ThinkingBubbleView: View {
    let elapsedSeconds: Int
    @State private var isAnimating = false

    private var formattedDuration: String {
        String(format: "%02d:%02d", elapsedSeconds / 60, elapsedSeconds % 60)
    }

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    ForEach(0..<3) { index in
                        Circle()
                            .fill(Color(.tertiaryLabel))
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
                .background(Color.surfaceSecondary)
                .cornerRadius(16)
                .overlay(
                    RoundedRectangle(cornerRadius: 16)
                        .stroke(Color(.separator), lineWidth: 0.5)
                )

                Text(formattedDuration)
                    .font(.caption2)
                    .foregroundColor(Color(.tertiaryLabel))
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
                    .fill(Color.blue.opacity(0.08))
                    .frame(width: 80, height: 80)
                    .scaleEffect(pulseScale)

                // Three bouncing dots
                HStack(spacing: 6) {
                    ForEach(0..<3) { index in
                        Circle()
                            .fill(Color.blue.opacity(0.7))
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
                    .foregroundColor(.primary)

                Text("Analyzing the article for you")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
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
