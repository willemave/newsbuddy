//
//  ContentDetailView.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import SwiftUI
import MarkdownUI
import UIKit
import os.log

private enum DiscussionTab: String, CaseIterable {
    case comments = "Comments"
    case links = "Links"
}

private enum DetailSheetDestination: String, Identifiable {
    case share
    case download
    case tweet
    case discussion
    case chat

    var id: String { rawValue }
}

private struct DetailImageAsset: Identifiable {
    let imageURL: URL
    let thumbnailURL: URL?

    var id: String { imageURL.absoluteString }
}

private struct ViewAlert: Identifiable {
    let id = UUID()
    let title: String
    let message: String
}

// MARK: - Design Tokens
private enum DetailDesign {
    // Spacing
    static let horizontalPadding: CGFloat = 20
    static let sectionSpacing: CGFloat = 20
    static let actionBarTopPadding: CGFloat = 0
    static let summaryTopPadding: CGFloat = 14
    static let cardPadding: CGFloat = 16

    // Corner radii
    static let cardRadius: CGFloat = 14
    static let buttonRadius: CGFloat = 10

    // Hero
    static let heroHeight: CGFloat = 220
    static let parallaxHeroHeight: CGFloat = 300
    static let parallaxRate: CGFloat = 0.25
}

private let detailLogger = Logger(subsystem: "com.newsly", category: "ContentDetailView")

struct ContentDetailView: View {
    let initialContentId: Int
    let initialContentType: ContentType?
    let allContentIds: [Int]
    @StateObject private var viewModel = ContentDetailViewModel()
    @StateObject private var chatSessionManager = ActiveChatSessionManager.shared
    @EnvironmentObject var readingStateStore: ReadingStateStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var dragAmount: CGFloat = 0
    @State private var currentIndex: Int
    // Navigation skipping state
    @State private var didTriggerNavigation: Bool = false
    @State private var navigationDirection: Int = 0 // +1 next, -1 previous
    // Convert button state
    @State private var isConverting: Bool = false
    // Modal presentation state
    @State private var activeSheet: DetailSheetDestination?
    @State private var isCheckingChatSession: Bool = false
    @State private var isStartingChat: Bool = false
    @State private var chatError: String?
    @StateObject private var narrationPlaybackService = NarrationPlaybackService.shared
    @State private var loadingNarrationTargets: Set<NarrationTarget> = []
    @State private var activeAlert: ViewAlert?
    // Full image viewer
    @State private var selectedImageAsset: DetailImageAsset?
    // Discussion sheet
    @State private var discussionPayload: ContentDiscussion?
    @State private var isLoadingDiscussion: Bool = false
    @State private var discussionFallbackURL: URL?
    @State private var discussionUnavailableMessage: String?
    @State private var discussionTab: DiscussionTab = .comments
    @State private var collapsedCommentIDs: Set<String> = Set()
    // Swipe haptic feedback
    @State private var didTriggerSwipeHaptic: Bool = false
    // Transcript/Full Article collapsed state
    @State private var isTranscriptExpanded: Bool = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    init(
        contentId: Int,
        contentType: ContentType? = nil,
        allContentIds: [Int] = []
    ) {
        self.initialContentId = contentId
        self.initialContentType = contentType
        self.allContentIds = allContentIds.isEmpty ? [contentId] : allContentIds
        if let index = allContentIds.firstIndex(of: contentId) {
            self._currentIndex = State(initialValue: index)
        } else {
            self._currentIndex = State(initialValue: 0)
        }
    }
    
    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                if viewModel.isLoading {
                    LoadingView()
                        .frame(minHeight: 400)
                } else if let error = viewModel.errorMessage {
                    ErrorView(message: error) {
                        Task { await viewModel.loadContent() }
                    }
                    .frame(minHeight: 400)
                } else if let content = viewModel.content {
                    VStack(alignment: .leading, spacing: 0) {
                        // Parallax hero header (image + title + action bar)
                        heroHeader(content: content)

                        Divider()
                            .padding(.horizontal, DetailDesign.horizontalPadding)

                        // Chat status banner (inline, under header)
                        if let activeSession = chatSessionManager.getSession(forContentId: content.id) {
                            ChatStatusBanner(
                                session: activeSession,
                                onTap: {
                                    openChatSession(sessionId: activeSession.id, contentId: content.id)
                                },
                                onDismiss: {
                                    chatSessionManager.markAsViewed(sessionId: activeSession.id)
                                },
                                style: .inline
                            )
                            .padding(.horizontal, DetailDesign.horizontalPadding)
                            .padding(.top, 12)
                        }

                        // Detected feed subscription card (news/self-submission when available)
                        if (content.canSubscribe ?? false), let feed = content.detectedFeed {
                            DetectedFeedCard(
                                feed: feed,
                                isSubscribing: viewModel.isSubscribingToFeed,
                                hasSubscribed: viewModel.feedSubscriptionSuccess,
                                subscriptionError: viewModel.feedSubscriptionError,
                                onSubscribe: {
                                    Task { await viewModel.subscribeToDetectedFeed() }
                                }
                            )
                            .padding(.horizontal, DetailDesign.horizontalPadding)
                            .padding(.top, 12)
                        }

                        // Summary Section (editorial v1, bulleted v1, interleaved v2, interleaved v1, or structured)
                        if let editorialSummary = content.editorialSummary {
                            EditorialNarrativeSummaryView(summary: editorialSummary, contentId: content.id)
                                .padding(.horizontal, DetailDesign.horizontalPadding)
                                .padding(.top, DetailDesign.summaryTopPadding)
                                .onAppear {
                                    logSummarySection(
                                        content: content,
                                        section: "editorial_v1",
                                        bulletPointCount: editorialSummary.keyPoints.count,
                                        insightCount: 0
                                    )
                                }
                        } else if let bulletedSummary = content.bulletedSummary {
                            BulletedSummaryView(summary: bulletedSummary, contentId: content.id)
                                .padding(.horizontal, DetailDesign.horizontalPadding)
                                .padding(.top, DetailDesign.summaryTopPadding)
                                .onAppear {
                                    logSummarySection(
                                        content: content,
                                        section: "bulleted_v1",
                                        bulletPointCount: bulletedSummary.points.count,
                                        insightCount: 0
                                    )
                                }
                        } else if let interleavedSummary = content.interleavedSummaryV2 {
                            InterleavedSummaryV2View(summary: interleavedSummary, contentId: content.id)
                                .padding(.horizontal, DetailDesign.horizontalPadding)
                                .padding(.top, DetailDesign.summaryTopPadding)
                                .onAppear {
                                    logSummarySection(
                                        content: content,
                                        section: "interleaved_v2",
                                        bulletPointCount: interleavedSummary.keyPoints.count,
                                        insightCount: 0
                                    )
                                }
                        } else if let interleavedSummary = content.interleavedSummary {
                            InterleavedSummaryView(summary: interleavedSummary, contentId: content.id)
                                .padding(.horizontal, DetailDesign.horizontalPadding)
                                .padding(.top, DetailDesign.summaryTopPadding)
                                .onAppear {
                                    logSummarySection(
                                        content: content,
                                        section: "interleaved_v1",
                                        bulletPointCount: 0,
                                        insightCount: interleavedSummary.insights.count
                                    )
                                }
                        } else if let structuredSummary = content.structuredSummary {
                            StructuredSummaryView(summary: structuredSummary, contentId: content.id)
                                .padding(.horizontal, DetailDesign.horizontalPadding)
                                .padding(.top, DetailDesign.summaryTopPadding)
                                .onAppear {
                                    logSummarySection(
                                        content: content,
                                        section: "structured",
                                        bulletPointCount: structuredSummary.bulletPoints.count,
                                        insightCount: 0
                                    )
                                }
                        }

                        if content.contentTypeEnum == .news {
                            if let newsMetadata = content.newsMetadata {
                                modernSectionPlain(isPadded: false) {
                                    NewsDigestDetailView(
                                        content: content,
                                        metadata: newsMetadata,
                                        onDiscussionTap: { url in
                                            handleDiscussionTap(content: content, fallbackURL: url)
                                        }
                                    )
                                }
                                .padding(.horizontal, DetailDesign.horizontalPadding)
                                .padding(.top, DetailDesign.sectionSpacing)
                            } else {
                                modernSectionPlain(isPadded: false) {
                                    VStack(alignment: .leading, spacing: 16) {
                                        sectionHeader("News Updates", icon: "newspaper")
                                        Text("No news metadata available.")
                                            .font(.subheadline)
                                            .foregroundColor(.secondary)
                                    }
                                }
                                .padding(.horizontal, DetailDesign.horizontalPadding)
                                .padding(.top, DetailDesign.sectionSpacing)
                            }
                        }

                        // Full Content Section (collapsible, modern style)
                        if let bodyText = viewModel.contentBody?.text {
                            modernExpandableSection(
                                title: content.contentTypeEnum == .podcast ? "Transcript" : "Full Article",
                                icon: content.contentTypeEnum == .podcast ? "text.alignleft" : "doc.text",
                                isExpanded: $isTranscriptExpanded
                            ) {
                                Markdown(bodyText)
                                    .markdownTheme(.gitHub)
                            }
                            .padding(.horizontal, DetailDesign.horizontalPadding)
                            .padding(.top, DetailDesign.sectionSpacing)
                        } else if content.contentTypeEnum == .podcast, let podcastMetadata = content.podcastMetadata, let transcript = podcastMetadata.transcript {
                            modernExpandableSection(
                                title: "Transcript",
                                icon: "text.alignleft",
                                isExpanded: $isTranscriptExpanded
                            ) {
                                Markdown(transcript)
                                    .markdownTheme(.gitHub)
                            }
                            .padding(.horizontal, DetailDesign.horizontalPadding)
                            .padding(.top, DetailDesign.sectionSpacing)
                        } else if let fullMarkdown = content.fullMarkdown {
                            modernExpandableSection(
                                title: content.contentTypeEnum == .podcast ? "Transcript" : "Full Article",
                                icon: "doc.text",
                                isExpanded: $isTranscriptExpanded
                            ) {
                                Markdown(fullMarkdown)
                                    .markdownTheme(.gitHub)
                            }
                            .padding(.horizontal, DetailDesign.horizontalPadding)
                            .padding(.top, DetailDesign.sectionSpacing)
                        }

                        // Bottom spacing
                        Spacer()
                            .frame(height: 40)
                    }
                }
            }
        }
        .coordinateSpace(name: "detailScroll")
        .textSelection(.enabled)
        .accessibilityIdentifier("content.detail.screen")
        .overlay(alignment: .leading) {
            // Left edge indicator (previous)
            if dragAmount > 30 && currentIndex > 0 {
                swipeIndicator(direction: .previous, progress: min(1.0, dragAmount / 100))
            }
        }
        .overlay(alignment: .trailing) {
            // Right edge indicator (next)
            if dragAmount < -30 && currentIndex < allContentIds.count - 1 {
                swipeIndicator(direction: .next, progress: min(1.0, abs(dragAmount) / 100))
            }
        }
        .offset(x: dragAmount)
        .animation(.interactiveSpring(response: 0.3, dampingFraction: 0.8), value: dragAmount)
        .simultaneousGesture(
            DragGesture(minimumDistance: 50, coordinateSpace: .global)
                .onChanged { value in
                    let horizontalAmount = abs(value.translation.width)
                    let verticalAmount = abs(value.translation.height)

                    // Require horizontal swipe
                    if horizontalAmount > verticalAmount * 2 && horizontalAmount > 30 {
                        // More responsive drag with resistance at edges
                        let canGoLeft = currentIndex < allContentIds.count - 1
                        let canGoRight = currentIndex > 0

                        var newOffset = value.translation.width * 0.6

                        // Add resistance if can't navigate in that direction
                        if newOffset < 0 && !canGoLeft {
                            newOffset = newOffset * 0.2
                        } else if newOffset > 0 && !canGoRight {
                            newOffset = newOffset * 0.2
                        }

                        dragAmount = newOffset

                        // Haptic feedback when crossing threshold
                        if abs(newOffset) > 80 && !didTriggerSwipeHaptic {
                            let generator = UIImpactFeedbackGenerator(style: .light)
                            generator.impactOccurred()
                            didTriggerSwipeHaptic = true
                        }
                    }
                }
                .onEnded { value in
                    didTriggerSwipeHaptic = false
                    let horizontalAmount = abs(value.translation.width)
                    let verticalAmount = abs(value.translation.height)

                    if horizontalAmount > verticalAmount * 2 && horizontalAmount > 80 {
                        if value.translation.width > 80 && currentIndex > 0 {
                            // Swipe right - previous
                            let generator = UIImpactFeedbackGenerator(style: .medium)
                            generator.impactOccurred()
                            withAnimation(.easeOut(duration: 0.2)) {
                                dragAmount = UIScreen.main.bounds.width
                            }
                            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                                // Reset without animation, then navigate
                                var transaction = Transaction()
                                transaction.disablesAnimations = true
                                withTransaction(transaction) {
                                    dragAmount = 0
                                }
                                navigateToPrevious()
                            }
                            return
                        } else if value.translation.width < -80 && currentIndex < allContentIds.count - 1 {
                            // Swipe left - next
                            let generator = UIImpactFeedbackGenerator(style: .medium)
                            generator.impactOccurred()
                            withAnimation(.easeOut(duration: 0.2)) {
                                dragAmount = -UIScreen.main.bounds.width
                            }
                            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                                // Reset without animation, then navigate
                                var transaction = Transaction()
                                transaction.disablesAnimations = true
                                withTransaction(transaction) {
                                    dragAmount = 0
                                }
                                navigateToNext()
                            }
                            return
                        }
                    }

                    // Snap back
                    withAnimation(.interactiveSpring(response: 0.3, dampingFraction: 0.8)) {
                        dragAmount = 0
                    }
                }
        )
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(.hidden, for: .navigationBar)
        // Hide the main tab bar while viewing details
        .toolbar(.hidden, for: .tabBar)
        .task {
            let idToLoad = allContentIds.isEmpty ? initialContentId : allContentIds[currentIndex]
            viewModel.updateContentId(idToLoad, contentType: initialContentType)
            await viewModel.loadContent()
        }
        .onChange(of: viewModel.content?.id) { _, newValue in
            guard let id = newValue, let content = viewModel.content else { return }
            if case .content(let activeContentId)? = narrationPlaybackService.speakingTarget,
               activeContentId != id {
                narrationPlaybackService.stop()
            }
            if let type = content.contentTypeEnum {
                readingStateStore.setCurrent(contentId: id, type: type)
            }
            logSummarySnapshot(content: content, context: "content_change")
        }
        // If user is navigating (chevrons or swipe), skip items that were already read
        .onChange(of: viewModel.wasAlreadyReadWhenLoaded) { _, wasRead in
            guard didTriggerNavigation, viewModel.content?.contentTypeEnum == .podcast else { return }
            if wasRead {
                let nextIndex = currentIndex + navigationDirection
                guard nextIndex >= 0 && nextIndex < allContentIds.count else {
                    // Reached the end; stop skipping further
                    didTriggerNavigation = false
                    navigationDirection = 0
                    return
                }
                currentIndex = nextIndex
                // Keep didTriggerNavigation/naviationDirection to allow cascading skips
            } else {
                // Landed on an unread item; reset navigation flags
                didTriggerNavigation = false
                navigationDirection = 0
            }
        }
        .onChange(of: currentIndex) { oldValue, newValue in
            Task {
                let newContentId = allContentIds[newValue]
                viewModel.updateContentId(newContentId, contentType: initialContentType)
                await viewModel.loadContent()
            }
        }
        .onDisappear {
            if let contentId = viewModel.content?.id,
               narrationPlaybackService.speakingTarget == .content(contentId) {
                narrationPlaybackService.stop()
            }
            readingStateStore.clear()
        }
        .alert(item: $activeAlert) { alert in
            Alert(
                title: Text(alert.title),
                message: Text(alert.message),
                dismissButton: .cancel(Text("OK"))
            )
        }
        .sheet(item: $activeSheet, onDismiss: {
            chatError = nil
            discussionUnavailableMessage = nil
        }) {
            switch $0 {
            case .share:
                shareSheet
                    .presentationDetents([.height(340)])
                    .presentationDragIndicator(.hidden)
                    .presentationCornerRadius(24)

            case .download:
                downloadSheet
                    .presentationDetents([.height(320)])
                    .presentationDragIndicator(.hidden)
                    .presentationCornerRadius(24)

            case .tweet:
                if let content = viewModel.content {
                    TweetSuggestionsSheet(contentId: content.id)
                }

            case .discussion:
                discussionSheet
                    .presentationDetents([.medium, .large])
                    .presentationDragIndicator(.visible)

            case .chat:
                if let content = viewModel.content {
                    chatSheet(content: content)
                        .presentationDetents([.height(380)])
                        .presentationDragIndicator(.hidden)
                        .presentationCornerRadius(24)
                }
            }
        }
    }

    // MARK: - Chat Helpers
    @MainActor
    private func handleChatButtonTapped() async {
        guard !isCheckingChatSession else { return }
        isCheckingChatSession = true
        defer { isCheckingChatSession = false }
        chatError = nil
        activeSheet = .chat
    }

    private func startChat(
        contentId: Int,
        provider: ChatModelProvider = .openai,
        prompt: String? = nil
    ) async {
        guard !isStartingChat else { return }

        isStartingChat = true
        chatError = nil

        do {
            let session = try await ChatService.shared.startArticleChat(
                contentId: contentId,
                provider: provider
            )
            var pendingResponse: SendChatMessageResponse?
            if let prompt, !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                pendingResponse = try await ChatService.shared.sendMessageAsync(
                    sessionId: session.id,
                    message: prompt
                )
            }
            activeSheet = nil
            openChatSession(
                sessionId: session.id,
                contentId: contentId,
                initialUserMessage: pendingResponse?.userMessage,
                pendingMessageId: pendingResponse?.messageId
            )
        } catch {
            chatError = error.localizedDescription
        }

        isStartingChat = false
    }

    private func startCouncilWithPrompt(
        _ prompt: String,
        contentId: Int,
        provider: ChatModelProvider = .openai
    ) async {
        guard !isStartingChat else { return }

        isStartingChat = true
        chatError = nil

        do {
            let session = try await ChatService.shared.startArticleChat(
                contentId: contentId,
                provider: provider
            )
            activeSheet = nil
            openChatSession(
                sessionId: session.id,
                contentId: contentId,
                pendingCouncilPrompt: session.isCouncilMode ? nil : prompt
            )
        } catch {
            chatError = error.localizedDescription
        }

        isStartingChat = false
    }

    private func deepDivePrompt(for content: ContentDetail) -> String {
        "Dig deeper into the key points of \(content.displayTitle). For each main point, explain reasoning, supporting evidence, and include a bit more detail explaining the point. Also pull out key ideas from the discussion context when available, and add more insights from the discussion, including notable agreements and disagreements. Keep answers concise and numbered."
    }

    private func councilPrompt(for content: ContentDetail) -> String {
        "Give me your perspective on \(content.displayTitle). Keep it short: 2-4 concise bullets on what matters most, what is weak or missing, and what actions or implications follow."
    }

    private func corroboratePrompt(for content: ContentDetail) -> String {
        "Corroborate the main claims in \(content.displayTitle) using recent, reputable sources. For each claim, list 2-3 supporting or conflicting sources with URLs, note disagreements, and flag gaps or weak evidence."
    }

    private func deepResearchPrompt(for content: ContentDetail) -> String {
        "Conduct comprehensive research on \(content.displayTitle). Find additional sources, verify claims, identify related developments, and provide a thorough analysis with citations."
    }

    private func startDeepResearchWithPrompt(_ prompt: String, contentId: Int) async {
        guard !isStartingChat else { return }

        isStartingChat = true
        chatError = nil

        do {
            let session = try await ChatService.shared.startDeepResearch(contentId: contentId)
            let pendingResponse = try await ChatService.shared.sendMessageAsync(
                sessionId: session.id,
                message: prompt
            )

            activeSheet = nil
            openChatSession(
                sessionId: session.id,
                contentId: contentId,
                initialUserMessage: pendingResponse.userMessage,
                pendingMessageId: pendingResponse.messageId
            )
        } catch {
            chatError = error.localizedDescription
        }

        isStartingChat = false
    }

    @MainActor
    private func openChatSession(
        sessionId: Int,
        contentId: Int,
        initialUserMessage: ChatMessage? = nil,
        pendingMessageId: Int? = nil,
        pendingCouncilPrompt: String? = nil
    ) {
        chatSessionManager.stopTracking(sessionId: sessionId)
        ChatNavigationCoordinator.shared.open(
            ChatSessionRoute(
                sessionId: sessionId,
                contentId: contentId,
                initialUserMessageText: initialUserMessage?.content,
                initialUserMessageTimestamp: initialUserMessage?.timestamp,
                pendingMessageId: pendingMessageId,
                pendingCouncilPrompt: pendingCouncilPrompt
            )
        )
    }

    @ViewBuilder
    private func audioPromptCard(for content: ContentDetail) -> some View {
        VStack(spacing: 10) {
            NarrationPressButton(
                isDisabled: isNarrationLoading(for: content),
                accessibilityLabel: narrationAccessibilityLabel(for: content),
                onTap: {
                    Task { await handleSummaryNarration(for: content) }
                },
                onSelectPlaybackSpeed: { option in
                    Task {
                        await handleSummaryNarration(
                            for: content,
                            rate: option.rate
                        )
                    }
                }
            ) {
                HStack(spacing: 12) {
                    Image(systemName: "text.quote")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundColor(.indigo)
                        .frame(width: 32, height: 32)
                        .background(Color.indigo.opacity(0.12))
                        .clipShape(RoundedRectangle(cornerRadius: 8))

                    VStack(alignment: .leading, spacing: 1) {
                        Text(
                            isNarrationActive(for: content)
                                ? "Stop summary narration"
                                : "Narrate summary here"
                        )
                        .font(.subheadline)
                        .fontWeight(.medium)
                        .foregroundColor(.primary)
                        Text(
                            isNarrationActive(for: content)
                                ? "End spoken playback"
                                : "Tap to listen at \(narrationPlaybackService.playbackSpeedTitle). Hold for speed options."
                        )
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.85)
                    }

                    Spacer()
                }
                .padding(10)
                .background(Color.surfaceSecondary)
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .accessibilityIdentifier("content.dictate_summary_live")
        }
    }

    private func supportsSummaryNarration(for content: ContentDetail) -> Bool {
        guard let type = content.contentTypeEnum else { return false }
        return type == .article || type == .news || type == .podcast
    }

    private func narrationTarget(for content: ContentDetail) -> NarrationTarget {
        .content(content.id)
    }

    @ViewBuilder
    private func narrationActionIcon(for content: ContentDetail, overlaid: Bool = false) -> some View {
        if isNarrationLoading(for: content) {
            ProgressView()
                .scaleEffect(0.8)
                .frame(width: 44, height: 44)
        } else if isNarrationActive(for: content) {
            minimalActionIcon("speaker.wave.3.fill", color: overlaid ? .white : .blue, overlaid: overlaid)
        } else {
            minimalActionIcon("speaker.wave.2", color: .secondary, overlaid: overlaid)
        }
    }

    private func narrationAccessibilityLabel(for content: ContentDetail) -> String {
        if isNarrationActive(for: content) {
            return "Stop summary narration"
        }
        return "Narrate summary at \(narrationPlaybackService.playbackSpeedTitle)"
    }

    private func isNarrationActive(for content: ContentDetail) -> Bool {
        narrationPlaybackService.isSpeaking
            && narrationPlaybackService.speakingTarget == narrationTarget(for: content)
    }

    private func isNarrationLoading(for content: ContentDetail) -> Bool {
        loadingNarrationTargets.contains(narrationTarget(for: content))
    }

    @MainActor
    private func handleSummaryNarration(
        for content: ContentDetail,
        rate: Float? = nil
    ) async {
        let target = narrationTarget(for: content)
        let playbackRate = rate ?? narrationPlaybackService.playbackRate
        if isNarrationActive(for: content),
           abs(narrationPlaybackService.playbackRate - playbackRate) < 0.001 {
            narrationPlaybackService.stop()
            return
        }

        loadingNarrationTargets.insert(target)
        defer { loadingNarrationTargets.remove(target) }

        do {
            try await narrationPlaybackService.playNarration(
                for: target,
                rate: playbackRate,
                fetchAudio: {
                    try await NarrationService.shared.fetchNarrationAudio(for: target)
                },
                fetchNarrationText: {
                    let response = try await NarrationService.shared.fetchNarration(for: target)
                    return response.narrationText
                }
            )
        } catch {
            activeAlert = ViewAlert(
                title: "Narration",
                message: "Failed to load narration: \(error.localizedDescription)"
            )
        }
    }

    // MARK: - Parallax Hero Header
    private var hasHeroImage: Bool {
        guard let content = viewModel.content,
              let imageUrlString = content.imageUrl,
              !imageUrlString.isEmpty,
              content.contentTypeEnum != .news,
              buildImageURL(from: imageUrlString) != nil else {
            return false
        }
        return true
    }

    @ViewBuilder
    private func heroHeader(content: ContentDetail) -> some View {
        if let imageUrlString = content.imageUrl,
           !imageUrlString.isEmpty,
           content.contentTypeEnum != .news,
           let imageUrl = buildImageURL(from: imageUrlString) {
            // Parallax hero with overlaid title + action bar
            let thumbnailUrl = content.thumbnailUrl.flatMap { buildImageURL(from: $0) }
            ZStack(alignment: .bottomLeading) {
                // Layer 1: Parallax image
                GeometryReader { geo in
                    let minY = geo.frame(in: .named("detailScroll")).minY
                    let isOverscroll = minY > 0
                    let scrolled = max(-minY, 0)
                    let rate = reduceMotion ? 0 : DetailDesign.parallaxRate
                    // Extra upward shift so image scrolls faster than content
                    let parallaxShift = scrolled * rate
                    // Overscroll stretch
                    let stretch = (isOverscroll && !reduceMotion) ? minY : 0
                    // Oversized image to prevent gaps during parallax
                    let extraHeight = geo.size.height * rate
                    let imageHeight = geo.size.height + geo.safeAreaInsets.top + stretch + extraHeight

                    Button {
                        selectedImageAsset = DetailImageAsset(
                            imageURL: imageUrl,
                            thumbnailURL: thumbnailUrl
                        )
                    } label: {
                        CachedAsyncImage(
                            url: imageUrl,
                            thumbnailUrl: thumbnailUrl
                        ) { image in
                            image
                                .resizable()
                                .aspectRatio(contentMode: .fill)
                                .frame(width: geo.size.width, height: imageHeight)
                                .clipped()
                        } placeholder: {
                            Rectangle()
                                .fill(Color(.systemGray5))
                                .frame(width: geo.size.width, height: imageHeight)
                                .overlay(ProgressView())
                        }
                    }
                    .buttonStyle(.plain)
                    .offset(y: -geo.safeAreaInsets.top - parallaxShift + (isOverscroll ? -minY : 0))
                }

                // Layer 2: Gradient scrim — blend into surfacePrimary
                LinearGradient(
                    gradient: Gradient(stops: [
                        .init(color: .clear, location: 0.0),
                        .init(color: .clear, location: 0.34),
                        .init(color: Color.black.opacity(0.22), location: 0.56),
                        .init(color: Color.black.opacity(0.72), location: 0.86),
                        .init(color: Color.surfacePrimary, location: 1.0)
                    ]),
                    startPoint: .top,
                    endPoint: .bottom
                )
                .allowsHitTesting(false)

                // Layer 3: Title + metadata + action bar
                VStack(alignment: .leading, spacing: 8) {
                    Text(content.displayTitle)
                        .font(.title3)
                        .fontWeight(.bold)
                        .foregroundColor(.white)
                        .shadow(color: .black.opacity(0.5), radius: 4, x: 0, y: 1)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("content.detail.title.\(content.id)")

                    HStack(spacing: 6) {
                        HStack(spacing: 4) {
                            Image(systemName: contentTypeIcon(for: content))
                                .font(.caption2)
                            Text(content.contentTypeEnum?.rawValue.capitalized ?? "Article")
                                .font(.caption)
                                .fontWeight(.medium)
                        }
                        .foregroundColor(.white.opacity(0.9))

                        if let source = content.source {
                            Text("·")
                                .foregroundColor(.white.opacity(0.5))
                            Text(source)
                                .font(.caption)
                                .foregroundColor(.white.opacity(0.8))
                        }

                        Text("·")
                            .foregroundColor(.white.opacity(0.5))

                        ContentTimestampText(
                            rawValue: content.primaryTimestamp,
                            style: .detailMeta,
                            fallback: "Recent"
                        )
                        .font(.caption)
                        .foregroundColor(.white.opacity(0.8))
                    }
                    .shadow(color: .black.opacity(0.4), radius: 3, x: 0, y: 1)

                    actionBar(content: content, overlaid: true)
                        .padding(.top, 2)
                }
                .padding(.horizontal, DetailDesign.horizontalPadding)
                .padding(.bottom, 10)
            }
            .frame(height: DetailDesign.parallaxHeroHeight)
            .clipped()
            .fullScreenCover(item: $selectedImageAsset) { asset in
                FullImageView(imageURL: asset.imageURL, thumbnailURL: asset.thumbnailURL)
            }
        } else {
            // No image fallback — standard layout
            VStack(alignment: .leading, spacing: 0) {
                Spacer().frame(height: 8)

                VStack(alignment: .leading, spacing: 8) {
                    Text(content.displayTitle)
                        .font(.title3)
                        .fontWeight(.bold)
                        .foregroundColor(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("content.detail.title.\(content.id)")

                    HStack(spacing: 6) {
                        HStack(spacing: 4) {
                            Image(systemName: contentTypeIcon(for: content))
                                .font(.caption2)
                            Text(content.contentTypeEnum?.rawValue.capitalized ?? "Article")
                                .font(.caption)
                                .fontWeight(.medium)
                        }
                        .foregroundColor(.accentColor)

                        if let source = content.source {
                            Text("·")
                                .foregroundColor(.secondary.opacity(0.4))
                            Text(source)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }

                        Text("·")
                            .foregroundColor(.secondary.opacity(0.4))

                        ContentTimestampText(
                            rawValue: content.primaryTimestamp,
                            style: .detailMeta,
                            fallback: "Recent"
                        )
                        .font(.caption)
                        .foregroundColor(.secondary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, DetailDesign.horizontalPadding)
                .padding(.top, 16)
                .padding(.bottom, 6)

                actionBar(content: content, overlaid: false)
                    .padding(.horizontal, DetailDesign.horizontalPadding)
                    .padding(.top, 2)
            }
        }
    }

    @ViewBuilder
    private func heroPlaceholder(content: ContentDetail) -> some View {
        Rectangle()
            .fill(
                LinearGradient(
                    colors: [
                        Color(.systemGray4),
                        Color(.systemGray5)
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .frame(height: DetailDesign.heroHeight)
            .overlay(
                Image(systemName: contentTypeIcon(for: content))
                    .font(.system(size: 56, weight: .ultraLight))
                    .foregroundColor(.white.opacity(0.3))
            )
    }

    private func contentTypeIcon(for content: ContentDetail) -> String {
        switch content.contentTypeEnum {
        case .article: return "doc.text"
        case .podcast: return "headphones"
        case .news: return "newspaper"
        case .none: return "doc.text"
        }
    }

    // MARK: - Modern Action Bar (Minimal, Twitter-inspired)
    @ViewBuilder
    private func actionBar(content: ContentDetail, overlaid: Bool = false) -> some View {
        HStack(spacing: 0) {
            // Primary action - Open in browser
            if let url = URL(string: content.url) {
                Link(destination: url) {
                    minimalActionIcon("safari", color: overlaid ? .white : .accentColor, overlaid: overlaid)
                }
                .accessibilityIdentifier("content.action.open_external")
            }

            Spacer()

            // Share
            Button(action: { activeSheet = .share }) {
                minimalActionIcon("square.and.arrow.up", overlaid: overlaid)
            }
            .accessibilityIdentifier("content.action.share")

            // Download more from series (article/podcast only)
            if content.contentTypeEnum == .article || content.contentTypeEnum == .podcast {
                Spacer()

                Button { activeSheet = .download } label: {
                    minimalActionIcon("tray.and.arrow.down", overlaid: overlaid)
                }
                .accessibilityIdentifier("content.action.download_more")
            }

            // Save linked article (news only)
            if content.contentTypeEnum == .news {
                Spacer()

                Button(action: {
                    Task {
                        isConverting = true
                        await viewModel.saveLinkedArticleAsKnowledge()
                        isConverting = false
                    }
                }) {
                    if isConverting {
                        ProgressView()
                            .scaleEffect(0.8)
                            .frame(width: 44, height: 44)
                    } else {
                        minimalActionIcon("books.vertical", overlaid: overlaid)
                    }
                }
                .disabled(isConverting)
                .accessibilityIdentifier("content.action.convert")
                .accessibilityLabel("Save linked article to Knowledge")
            }

            Spacer()

            // Favorite
            Button(action: {
                Task { await viewModel.toggleFavorite() }
            }) {
                let favColor: Color = content.isFavorited ? .yellow : (overlaid ? .white : .secondary)
                minimalActionIcon(
                    content.isFavorited ? "star.fill" : "star",
                    color: favColor,
                    overlaid: overlaid
                )
            }
            .accessibilityIdentifier("content.action.favorite")

            if supportsSummaryNarration(for: content) {
                Spacer()

                NarrationPressButton(
                    isDisabled: isNarrationLoading(for: content),
                    accessibilityLabel: narrationAccessibilityLabel(for: content),
                    onTap: {
                        Task { await handleSummaryNarration(for: content) }
                    },
                    onSelectPlaybackSpeed: { option in
                        Task {
                            await handleSummaryNarration(
                                for: content,
                                rate: option.rate
                            )
                        }
                    }
                ) {
                    narrationActionIcon(for: content, overlaid: overlaid)
                }
                .accessibilityIdentifier("content.action.narrate_summary")
            }

            Spacer()

            // Deep Dive chat
            Button(action: {
                Task {
                    if let activeSession = chatSessionManager.getSession(forContentId: content.id) {
                        openChatSession(sessionId: activeSession.id, contentId: content.id)
                        return
                    }
                    await handleChatButtonTapped()
                }
            }) {
                if isStartingChat {
                    Image(systemName: "brain.head.profile")
                        .font(.system(size: 20, weight: .regular))
                        .foregroundColor(overlaid ? .white : .accentColor)
                        .shadow(color: overlaid ? .black.opacity(0.4) : .clear, radius: 3, x: 0, y: 1)
                        .frame(width: 44, height: 44)
                        .symbolEffect(.pulse, options: .repeating)
                } else {
                    minimalActionIcon("brain.head.profile", overlaid: overlaid)
                }
            }
            .disabled(isCheckingChatSession)
            .accessibilityIdentifier("content.action.deep_dive")
        }
        .frame(height: 44)
    }

    @ViewBuilder
    private func minimalActionIcon(_ icon: String, color: Color = .secondary, overlaid: Bool = false) -> some View {
        Image(systemName: icon)
            .font(.system(size: 20, weight: .regular))
            .foregroundColor(overlaid ? (color == .secondary ? .white : color) : color)
            .shadow(color: overlaid ? .black.opacity(0.4) : .clear, radius: 3, x: 0, y: 1)
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
    }

    // MARK: - Mini Sheet Components

    @ViewBuilder
    private func sheetHeader(title: String, dismiss: @escaping () -> Void) -> some View {
        VStack(spacing: 0) {
            RoundedRectangle(cornerRadius: 2.5)
                .fill(Color.secondary.opacity(0.3))
                .frame(width: 36, height: 5)
                .padding(.top, 8)

            HStack {
                Text(title)
                    .font(.title3)
                    .fontWeight(.bold)
                Spacer()
                Button(action: dismiss) {
                    Image(systemName: "xmark")
                        .font(.body)
                        .fontWeight(.semibold)
                        .foregroundColor(.secondary)
                        .frame(width: 44, height: 44)
                        .background(Color(.tertiarySystemBackground))
                        .clipShape(Circle())
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 14)
            .padding(.bottom, 16)
        }
    }

    @ViewBuilder
    private func sheetOptionRow(
        icon: String,
        iconColor: Color = .accentColor,
        title: String,
        subtitle: String,
        badge: String? = nil,
        disabled: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .font(.system(size: 18, weight: .medium))
                    .foregroundColor(iconColor)
                    .frame(width: 36, height: 36)
                    .background(iconColor.opacity(0.1))
                    .clipShape(RoundedRectangle(cornerRadius: 8))

                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.body)
                        .fontWeight(.medium)
                        .foregroundColor(.primary)
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }

                Spacer()

                if let badge {
                    Text(badge)
                        .font(.caption)
                        .fontWeight(.medium)
                        .foregroundColor(.secondary)
                }
            }
            .padding(.vertical, 12)
            .padding(.horizontal, 12)
            .frame(minHeight: RowMetrics.compactHeight)
            .background(Color.surfaceSecondary)
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
        .disabled(disabled)
    }

    // MARK: - Share Sheet
    @ViewBuilder
    private var shareSheet: some View {
        VStack(spacing: 0) {
            sheetHeader(title: "Share") { activeSheet = nil }

            VStack(spacing: 8) {
                sheetOptionRow(
                    icon: "link",
                    title: "Title + link",
                    subtitle: "Headline and URL only",
                    action: {
                        activeSheet = nil
                        viewModel.shareContent(option: .light)
                    }
                )
                sheetOptionRow(
                    icon: "text.quote",
                    title: "Key points",
                    subtitle: "Summary, top quotes, and link",
                    action: {
                        activeSheet = nil
                        viewModel.shareContent(option: .medium)
                    }
                )
                sheetOptionRow(
                    icon: "doc.plaintext",
                    title: "Full content",
                    subtitle: "Complete article or transcript",
                    action: {
                        activeSheet = nil
                        viewModel.shareContent(option: .full)
                    }
                )
            }
            .padding(.horizontal, 20)

            Divider()
                .padding(.horizontal, 20)
                .padding(.vertical, 12)

            sheetOptionRow(
                icon: "at",
                title: "Tweet suggestions",
                subtitle: "Generate tweet-ready snippets",
                action: {
                    activeSheet = nil
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                        activeSheet = .tweet
                    }
                }
            )
            .padding(.horizontal, 20)

            Spacer(minLength: 0)
        }
        .background(Color.surfacePrimary)
    }

    // MARK: - Download Sheet
    @ViewBuilder
    private var downloadSheet: some View {
        VStack(spacing: 0) {
            sheetHeader(title: "Load more from series") { activeSheet = nil }

            VStack(spacing: 8) {
                sheetOptionRow(
                    icon: "square.stack",
                    iconColor: .secondary,
                    title: "3 episodes",
                    subtitle: "Quick catch-up",
                    action: {
                        activeSheet = nil
                        Task { await viewModel.downloadMoreFromSeries(count: 3) }
                    }
                )
                sheetOptionRow(
                    icon: "square.stack",
                    iconColor: .secondary,
                    title: "5 episodes",
                    subtitle: "Recent backlog",
                    action: {
                        activeSheet = nil
                        Task { await viewModel.downloadMoreFromSeries(count: 5) }
                    }
                )
                sheetOptionRow(
                    icon: "square.stack.3d.up",
                    iconColor: .secondary,
                    title: "10 episodes",
                    subtitle: "Deep dive into the series",
                    action: {
                        activeSheet = nil
                        Task { await viewModel.downloadMoreFromSeries(count: 10) }
                    }
                )
                sheetOptionRow(
                    icon: "square.stack.3d.up.fill",
                    iconColor: .secondary,
                    title: "20 episodes",
                    subtitle: "Full archive pull",
                    action: {
                        activeSheet = nil
                        Task { await viewModel.downloadMoreFromSeries(count: 20) }
                    }
                )
            }
            .padding(.horizontal, 20)

            Spacer(minLength: 0)
        }
        .background(Color.surfacePrimary)
    }

    // MARK: - AI Chat Sheet
    @ViewBuilder
    private func chatSheet(content: ContentDetail) -> some View {
        VStack(spacing: 0) {
            sheetHeader(title: "AI Chat") { activeSheet = nil }

            VStack(spacing: 8) {
                if let chatError {
                    HStack(spacing: 8) {
                        Image(systemName: "exclamationmark.circle.fill")
                            .foregroundColor(.red)
                        Text(chatError)
                            .font(.footnote)
                            .foregroundColor(.red)
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.red.opacity(0.1))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }

                sheetOptionRow(
                    icon: "message",
                    iconColor: .accentColor,
                    title: "Start chat",
                    subtitle: "Ask your own question about this story",
                    disabled: isStartingChat,
                    action: {
                        Task {
                            await startChat(
                                contentId: content.id,
                                provider: .openai
                            )
                        }
                    }
                )
                sheetOptionRow(
                    icon: "doc.text.magnifyingglass",
                    iconColor: .blue,
                    title: "Dig deeper",
                    subtitle: "Explore key points in detail",
                    disabled: isStartingChat,
                    action: {
                        Task {
                            await startChat(
                                contentId: content.id,
                                provider: .openai,
                                prompt: deepDivePrompt(for: content)
                            )
                        }
                    }
                )
                sheetOptionRow(
                    icon: "person.3.sequence.fill",
                    iconColor: .orange,
                    title: "Council Chat",
                    subtitle: "Compare four saved perspectives",
                    disabled: isStartingChat,
                    action: {
                        Task {
                            await startCouncilWithPrompt(
                                councilPrompt(for: content),
                                contentId: content.id,
                                provider: .openai
                            )
                        }
                    }
                )
                sheetOptionRow(
                    icon: "checkmark.shield",
                    iconColor: .green,
                    title: "Corroborate",
                    subtitle: "Verify claims with sources",
                    disabled: isStartingChat,
                    action: {
                        Task {
                            await startChat(
                                contentId: content.id,
                                provider: .openai,
                                prompt: corroboratePrompt(for: content)
                            )
                        }
                    }
                )
                sheetOptionRow(
                    icon: "magnifyingglass.circle.fill",
                    iconColor: .purple,
                    title: "Deep Research",
                    subtitle: "Comprehensive analysis with sources",
                    badge: "~2-5 min",
                    disabled: isStartingChat,
                    action: {
                        Task { await startDeepResearchWithPrompt(deepResearchPrompt(for: content), contentId: content.id) }
                    }
                )
            }
            .padding(.horizontal, 20)

            Divider()
                .padding(.horizontal, 20)
                .padding(.vertical, 12)

            if supportsSummaryNarration(for: content) {
                audioPromptCard(for: content)
                    .padding(.horizontal, 20)
            }

            Spacer(minLength: 0)
        }
        .background(Color.surfacePrimary)
    }

    private func handleDiscussionTap(content: ContentDetail, fallbackURL: URL) {
        discussionFallbackURL = fallbackURL
        discussionUnavailableMessage = nil
        activeSheet = .discussion
        Task { await loadDiscussion(content: content, fallbackURL: fallbackURL) }
    }

    @MainActor
    private func loadDiscussion(content: ContentDetail, fallbackURL: URL) async {
        discussionFallbackURL = fallbackURL
        if isLoadingDiscussion {
            activeSheet = .discussion
            return
        }
        isLoadingDiscussion = true
        discussionPayload = nil
        discussionUnavailableMessage = nil
        activeSheet = .discussion
        defer { isLoadingDiscussion = false }

        do {
            let discussion = try await ContentService.shared.fetchContentDiscussion(
                id: content.id,
                contentType: content.contentTypeEnum
            )
            discussionPayload = discussion
            if discussion.hasRenderableContent {
                discussionTab = .comments
                collapsedCommentIDs = []
            } else {
                discussionUnavailableMessage = discussion.unavailableMessage
            }
        } catch {
            discussionUnavailableMessage = "Comments could not be loaded right now."
        }
    }

    @ViewBuilder
    private var discussionSheet: some View {
        NavigationStack {
            Group {
                if isLoadingDiscussion {
                    discussionLoadingView
                } else if let discussion = discussionPayload, discussion.hasRenderableContent {
                    if discussion.mode == "discussion_list" {
                        // Techmeme-style grouped links — no tabs
                        ScrollView {
                            VStack(alignment: .leading, spacing: 16) {
                                if discussion.discussionGroups.isEmpty {
                                    Text("No discussion links available.")
                                        .font(.subheadline)
                                        .foregroundColor(.secondary)
                                } else {
                                    ForEach(discussion.discussionGroups) { group in
                                        VStack(alignment: .leading, spacing: 8) {
                                            Text(group.label)
                                                .font(.headline)
                                            ForEach(group.items) { item in
                                                if let url = URL(string: item.url) {
                                                    Link(destination: url) {
                                                        HStack(spacing: 8) {
                                                            Image(systemName: "arrow.up.right.square")
                                                            Text(item.title)
                                                                .multilineTextAlignment(.leading)
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                        .padding(.bottom, 4)
                                    }
                                }
                            }
                            .padding(.horizontal, 20)
                            .padding(.vertical, 16)
                        }
                    } else {
                        // Comments mode — segmented tabs
                        VStack(spacing: 0) {
                            if !discussion.links.isEmpty {
                                Picker("Tab", selection: $discussionTab) {
                                    ForEach(DiscussionTab.allCases, id: \.self) { tab in
                                        Text(tab.rawValue).tag(tab)
                                    }
                                }
                                .pickerStyle(.segmented)
                                .padding(.horizontal, 20)
                                .padding(.vertical, 10)
                            }

                            ScrollView {
                                let commentIndex = buildDiscussionCommentIndex(from: discussion.comments)
                                switch discussionTab {
                                case .comments:
                                    commentsTabContent(commentIndex: commentIndex)
                                case .links:
                                    linksTabContent(discussion: discussion, commentsByID: commentIndex.commentsByID)
                                }
                            }
                        }
                    }
                } else {
                    discussionUnavailableView
                }
            }
            .navigationTitle("Discussion")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { activeSheet = nil }
                }
            }
        }
        .accessibilityIdentifier("content.discussion.sheet")
    }

    @ViewBuilder
    private var discussionLoadingView: some View {
        VStack(spacing: 12) {
            ProgressView()
                .controlSize(.large)
            Text("Loading discussion…")
                .font(.subheadline)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }

    @ViewBuilder
    private var discussionUnavailableView: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Discussion unavailable")
                    .font(.headline)

                Text(discussionUnavailableText)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let url = discussionResolvedFallbackURL {
                Button {
                    activeSheet = nil
                    openURL(url)
                } label: {
                    Label("Open original discussion", systemImage: "arrow.up.right.square")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
            }

            if let content = viewModel.content, let url = discussionResolvedFallbackURL {
                Button("Try again") {
                    Task { await loadDiscussion(content: content, fallbackURL: url) }
                }
                .buttonStyle(.bordered)
            }

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding(20)
    }

    private var discussionResolvedFallbackURL: URL? {
        if let discussionURL = discussionPayload?.discussionURL,
           let url = URL(string: discussionURL) {
            return url
        }
        return discussionFallbackURL
    }

    private var discussionUnavailableText: String {
        if let discussionUnavailableMessage {
            return discussionUnavailableMessage
        }
        if let discussionPayload {
            return discussionPayload.unavailableMessage
        }
        return "No discussion is available for this story."
    }

    private struct DiscussionCommentIndex {
        let orderedComments: [DiscussionComment]
        let commentsByID: [String: DiscussionComment]
        let descendantCountByID: [String: Int]
    }

    /// Build one reusable index for comment rendering.
    private func buildDiscussionCommentIndex(from comments: [DiscussionComment]) -> DiscussionCommentIndex {
        guard !comments.isEmpty else {
            return DiscussionCommentIndex(orderedComments: [], commentsByID: [:], descendantCountByID: [:])
        }

        var commentsByID: [String: DiscussionComment] = [:]
        var childrenByParentID: [String: [DiscussionComment]] = [:]
        var roots: [DiscussionComment] = []

        for comment in comments {
            commentsByID[comment.commentID] = comment
            if let parentID = comment.parentID {
                childrenByParentID[parentID, default: []].append(comment)
            } else {
                roots.append(comment)
            }
        }

        if roots.isEmpty {
            roots = comments.filter { $0.depth == 0 }
        }
        if roots.isEmpty {
            roots = comments
        }

        var orderedComments: [DiscussionComment] = []
        var stack = Array(roots.reversed())
        while let current = stack.popLast() {
            orderedComments.append(current)
            if let children = childrenByParentID[current.commentID] {
                for child in children.reversed() {
                    stack.append(child)
                }
            }
        }

        var descendantCountByID: [String: Int] = [:]

        func computeDescendantCount(for commentID: String) -> Int {
            if let cached = descendantCountByID[commentID] {
                return cached
            }

            let children = childrenByParentID[commentID] ?? []
            let total = children.reduce(0) { partialResult, child in
                partialResult + 1 + computeDescendantCount(for: child.commentID)
            }
            descendantCountByID[commentID] = total
            return total
        }

        for comment in comments {
            _ = computeDescendantCount(for: comment.commentID)
        }

        return DiscussionCommentIndex(
            orderedComments: orderedComments,
            commentsByID: commentsByID,
            descendantCountByID: descendantCountByID
        )
    }

    /// Whether a comment should be hidden because an ancestor is collapsed.
    private func isHiddenByCollapse(
        _ comment: DiscussionComment,
        commentsByID: [String: DiscussionComment]
    ) -> Bool {
        guard !collapsedCommentIDs.isEmpty else { return false }
        var current = comment
        while let pid = current.parentID, let parent = commentsByID[pid] {
            if collapsedCommentIDs.contains(parent.commentID) {
                return true
            }
            current = parent
        }
        return false
    }

    @ViewBuilder
    private func commentsTabContent(commentIndex: DiscussionCommentIndex) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if commentIndex.orderedComments.isEmpty {
                Text("No comments available.")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .padding(.top, 20)
                    .frame(maxWidth: .infinity)
            } else {
                ForEach(commentIndex.orderedComments) { comment in
                    if !isHiddenByCollapse(comment, commentsByID: commentIndex.commentsByID) {
                        let indent = CGFloat(min(comment.depth, 5)) * 16
                        let isCollapsed = collapsedCommentIDs.contains(comment.commentID)
                        let childCount = commentIndex.descendantCountByID[comment.commentID] ?? 0

                        VStack(alignment: .leading, spacing: 6) {
                            HStack(spacing: 6) {
                                Text(comment.author ?? "unknown")
                                    .font(.caption)
                                    .fontWeight(.medium)
                                    .foregroundColor(.secondary)

                                if isCollapsed && childCount > 0 {
                                    Text("+\(childCount)")
                                        .font(.caption2)
                                        .fontWeight(.semibold)
                                        .foregroundColor(.orange)
                                        .padding(.horizontal, 5)
                                        .padding(.vertical, 1)
                                        .background(Color.orange.opacity(0.12))
                                        .clipShape(Capsule())
                                }

                                Spacer()

                                if childCount > 0 {
                                    Image(systemName: isCollapsed ? "chevron.right" : "chevron.down")
                                        .font(.caption2)
                                        .foregroundColor(.secondary.opacity(0.6))
                                }
                            }

                            if !isCollapsed {
                                Text(comment.compactText ?? comment.text)
                                    .font(.callout)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .padding(12)
                        .background(Color.surfaceSecondary)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .overlay(alignment: .leading) {
                            if comment.depth > 0 {
                                RoundedRectangle(cornerRadius: 1.5)
                                    .fill(Color.orange)
                                    .frame(width: 3)
                                    .padding(.vertical, 4)
                            }
                        }
                        .padding(.leading, indent)
                        .accessibilityIdentifier("content.discussion.comment.\(comment.commentID)")
                        .contentShape(Rectangle())
                        .onTapGesture {
                            guard childCount > 0 else { return }
                            withAnimation(.easeInOut(duration: 0.2)) {
                                if isCollapsed {
                                    collapsedCommentIDs.remove(comment.commentID)
                                } else {
                                    collapsedCommentIDs.insert(comment.commentID)
                                }
                            }
                        }
                    }
                }
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 16)
    }

    @ViewBuilder
    private func linksTabContent(
        discussion: ContentDiscussion,
        commentsByID: [String: DiscussionComment]
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if discussion.links.isEmpty {
                Text("No links found.")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .padding(.top, 20)
                    .frame(maxWidth: .infinity)
            } else {
                ForEach(discussion.links) { link in
                    if let url = URL(string: link.url) {
                        Link(destination: url) {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(link.title ?? link.url)
                                    .font(.callout)
                                    .fontWeight(.medium)
                                    .foregroundColor(.primary)
                                    .multilineTextAlignment(.leading)
                                    .lineLimit(2)

                                Text(link.url)
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)

                                // Show originating comment snippet
                                if let commentID = link.commentID,
                                   let comment = commentsByID[commentID] {
                                    Text(comment.compactText ?? String(comment.text.prefix(120)))
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                        .lineLimit(2)
                                        .padding(.top, 2)
                                }

                                HStack(spacing: 4) {
                                    Image(systemName: "arrow.up.right")
                                        .font(.caption2)
                                    Text(link.source)
                                        .font(.caption2)
                                }
                                .foregroundColor(.accentColor)
                            }
                            .padding(12)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.surfaceSecondary)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                    }
                }
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 16)
    }

    // MARK: - Modern Section Components (Flat, no borders)
    @ViewBuilder
    private func modernSectionCard<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        content()
            .padding(DetailDesign.cardPadding)
            .background(
                RoundedRectangle(cornerRadius: DetailDesign.cardRadius)
                    .fill(Color.surfaceSecondary)
            )
            .overlay(
                RoundedRectangle(cornerRadius: DetailDesign.cardRadius)
                    .stroke(Color(.separator).opacity(0.6), lineWidth: 1)
            )
    }

    @ViewBuilder
    private func modernSectionPlain<Content: View>(isPadded: Bool = true, @ViewBuilder content: () -> Content) -> some View {
        content()
            .padding(isPadded ? DetailDesign.cardPadding : 0)
    }

    @ViewBuilder
    private func modernExpandableSection<Content: View>(
        title: String,
        icon: String,
        isExpanded: Binding<Bool>,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.25)) {
                    isExpanded.wrappedValue.toggle()
                }
            } label: {
                HStack {
                    HStack(spacing: 8) {
                        Image(systemName: icon)
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                        Text(title)
                            .font(.subheadline)
                            .fontWeight(.semibold)
                            .foregroundColor(.primary)
                    }

                    Spacer()

                    Image(systemName: "chevron.right")
                        .font(.caption2)
                        .fontWeight(.bold)
                        .foregroundColor(.secondary.opacity(0.6))
                        .rotationEffect(.degrees(isExpanded.wrappedValue ? 90 : 0))
                }
                .padding(DetailDesign.cardPadding)
            }
            .buttonStyle(.plain)

            if isExpanded.wrappedValue {
                content()
                    .padding(.horizontal, DetailDesign.cardPadding)
                    .padding(.bottom, DetailDesign.cardPadding)
            }
        }
        .background(Color.surfaceSecondary)
        .clipShape(RoundedRectangle(cornerRadius: DetailDesign.cardRadius))
    }

    @ViewBuilder
    private func sectionHeader(_ title: String, icon: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .font(.subheadline)
                .foregroundColor(.secondary)
            Text(title)
                .font(.subheadline)
                .fontWeight(.semibold)
        }
    }

    private func buildImageURL(from urlString: String) -> URL? {
        // If it's already a full URL, use it
        if urlString.hasPrefix("http://") || urlString.hasPrefix("https://") {
            return URL(string: urlString)
        }
        // Otherwise, it's a relative path - prepend base URL
        // Use string concatenation instead of appendingPathComponent to preserve path structure
        let baseURL = AppSettings.shared.baseURL
        let fullURL = urlString.hasPrefix("/") ? baseURL + urlString : baseURL + "/" + urlString
        return URL(string: fullURL)
    }

    // MARK: - Swipe Indicator
    private enum SwipeDirection {
        case previous, next
    }

    @ViewBuilder
    private func swipeIndicator(direction: SwipeDirection, progress: CGFloat) -> some View {
        let iconName = direction == .previous ? "chevron.left" : "chevron.right"

        VStack {
            Spacer()
            HStack {
                if direction == .next { Spacer() }
                Image(systemName: iconName)
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundColor(.white)
                    .frame(width: 44, height: 44)
                    .background(
                        Circle()
                            .fill(Color.accentColor.opacity(0.9))
                    )
                    .scaleEffect(0.8 + (progress * 0.4))
                    .opacity(Double(progress))
                    .padding(.horizontal, 8)
                if direction == .previous { Spacer() }
            }
            Spacer()
        }
    }

    private var statusIcon: String {
        guard let content = viewModel.content else { return "circle" }
        switch content.status {
        case "completed":
            return "checkmark.circle.fill"
        case "failed":
            return "xmark.circle.fill"
        case "processing":
            return "arrow.clockwise.circle.fill"
        default:
            return "circle"
        }
    }
    
    private var statusColor: Color {
        guard let content = viewModel.content else { return .secondary }
        switch content.status {
        case "completed":
            return .green
        case "failed":
            return .red
        case "processing":
            return .orange
        default:
            return .secondary
        }
    }

    private func logSummarySnapshot(content: ContentDetail, context: String) {
        let structuredCount = content.structuredSummary?.bulletPoints.count ?? 0
        let interleavedV1Count = content.interleavedSummary?.insights.count ?? 0
        let interleavedV2Count = content.interleavedSummaryV2?.keyPoints.count ?? 0
        let bulletedCount = content.bulletedSummary?.points.count ?? 0
        let editorialCount = content.editorialSummary?.keyPoints.count ?? 0
        detailLogger.info(
            "[ContentDetailView] summary snapshot (\(context)) id=\(content.id) type=\(content.contentType, privacy: .public) editorial_v1=\(content.editorialSummary != nil) bulleted_v1=\(content.bulletedSummary != nil) structured=\(content.structuredSummary != nil) interleaved_v1=\(content.interleavedSummary != nil) interleaved_v2=\(content.interleavedSummaryV2 != nil) editorial_key_points=\(editorialCount) bulleted_points=\(bulletedCount) structured_points=\(structuredCount) interleaved_insights=\(interleavedV1Count) interleaved_key_points=\(interleavedV2Count) raw_bullets=\(content.bulletPoints.count)"
        )
    }

    private func logSummarySection(
        content: ContentDetail,
        section: String,
        bulletPointCount: Int,
        insightCount: Int
    ) {
        detailLogger.info(
            "[ContentDetailView] summary section (\(section)) id=\(content.id) type=\(content.contentType, privacy: .public) points=\(bulletPointCount) insights=\(insightCount)"
        )
    }
    
    private func navigateToNext() {
        guard currentIndex < allContentIds.count - 1 else {
            return
        }
        didTriggerNavigation = true
        navigationDirection = 1
        currentIndex += 1
    }
    
    private func navigateToPrevious() {
        guard currentIndex > 0 else {
            return
        }
        didTriggerNavigation = true
        navigationDirection = -1
        currentIndex -= 1
    }
}
