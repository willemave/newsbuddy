//
//  ContentDetailView.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import SwiftUI
import UIKit
import os.log

private enum DiscussionTab: String, CaseIterable {
    case comments = "COMMENTS"
    case links = "LINKS"
}

private enum DetailSheetDestination: String, Identifiable {
    case share
    case download
    case tweet
    case discussion
    case chat

    var id: String { rawValue }
}

private struct BrowserDestination: Identifiable {
    let url: URL

    var id: String { url.absoluteString }
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
    static let horizontalPadding: CGFloat = Spacing.appHorizontalMargin
    static let headerHorizontalPadding: CGFloat = horizontalPadding
    static let sectionSpacing: CGFloat = 20
    static let actionBarTopPadding: CGFloat = 0
    static let summaryTopPadding: CGFloat = 14
    static let cardPadding: CGFloat = 16

    // Corner radii
    static let cardRadius: CGFloat = 14
    static let buttonRadius: CGFloat = 10

    // Hero
    static let heroHeight: CGFloat = 220
    static let parallaxHeroHeight: CGFloat = 260
    static let parallaxRate: CGFloat = 0.25
    static let floatingBackButtonSize: CGFloat = 44
    static let textOnlyBackButtonTopPadding: CGFloat = 8
    static let textOnlyTitleTopPadding: CGFloat = 18
    static let textOnlyNewsHeaderTopSpacer: CGFloat = 42
    static let textOnlyStandardHeaderTopSpacer: CGFloat = 48
    static let actionIconOpticalInset: CGFloat = 12
    static let edgeNavigationSwipeWidth: CGFloat = 44
    static let edgeNavigationTopExclusionHeight: CGFloat = 120
}

private let detailLogger = Logger(subsystem: "com.newsly", category: "ContentDetailView")

struct ContentDetailView: View {
    private let navigationContext: ContentDetailNavigationContext
    private var initialContentId: Int { navigationContext.initialContentId }
    private var initialContentType: APIContentType? { navigationContext.initialContentType }
    private var allContentIds: [Int] { navigationContext.contentIds }
    @StateObject private var viewModel = ContentDetailViewModel()
    @StateObject private var chatSessionManager = ActiveChatSessionManager.shared
    @EnvironmentObject var readingStateStore: ReadingStateStore
    @Environment(\.dismiss) private var dismiss
    @State private var currentIndex: Int
    // Navigation skipping state
    @State private var didTriggerNavigation: Bool = false
    @State private var navigationDirection: Int = 0 // +1 next, -1 previous
    // Convert button state
    @State private var isConverting: Bool = false
    // Modal presentation state
    @State private var activeSheet: DetailSheetDestination?
    @State private var pendingShareOption: ShareContentOption?
    @State private var isCheckingChatSession: Bool = false
    @State private var isStartingChat: Bool = false
    @State private var chatError: String?
    @StateObject private var narrationPlaybackService = NarrationPlaybackService.shared
    @State private var loadingAudioEpisodeContentIds: Set<Int> = []
    @State private var audioEpisodeByContentId: [Int: AudioEpisode] = [:]
    @State private var activeAlert: ViewAlert?
    @State private var activeReaderContent: ContentDetail?
    @State private var activeBrowserDestination: BrowserDestination?
    @State private var activeLearningDeckReader: LearningDeckReaderDestination?
    @State private var showLearningDeckCreateSheet = false
    @AppStorage("hasSeenLearningDeckHint") private var hasSeenLearningDeckHint = false
    @State private var showLearningDeckHint = false
    @State private var learningDeckHintBounce = false
    // Full image viewer
    @State private var selectedImageAsset: DetailImageAsset?
    // Discussion sheet
    @State private var discussionPayload: ContentDiscussion?
    @State private var isLoadingDiscussion: Bool = false
    @State private var discussionFallbackURL: URL?
    @State private var discussionUnavailableMessage: String?
    @State private var discussionTab: DiscussionTab = .comments
    @State private var collapsedCommentIDs: Set<String> = Set()
    @State private var discussionRequestToken = UUID()
    // Transcript/Full Article collapsed state
    @State private var isTranscriptExpanded: Bool = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    init(
        contentId: Int,
        contentType: APIContentType? = nil,
        allContentIds: [Int] = [],
        navigationSurface: ContentDetailNavigationSurface = .direct
    ) {
        let context = ContentDetailNavigationContext(
            initialContentId: contentId,
            initialContentType: contentType,
            contentIds: allContentIds,
            surface: navigationSurface
        )
        self.navigationContext = context
        self._currentIndex = State(initialValue: context.initialIndex)
    }
    
    var body: some View {
        ContentDetailSwipeContainer(
            currentIndex: currentIndex,
            contentIds: allContentIds,
            surfaceName: navigationSurfaceName,
            edgeWidth: DetailDesign.edgeNavigationSwipeWidth,
            topHitExclusionHeight: DetailDesign.edgeNavigationTopExclusionHeight,
            leadingEdgePreviousEnabled: navigationContext.surface != .fastNews,
            onDismiss: { dismiss() },
            onNext: navigateToNext,
            onPrevious: navigateToPrevious
        ) {
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
                            if let activeSession = activeChatSession(for: content) {
                                ChatStatusBanner(
                                    session: activeSession,
                                    onTap: {
                                        openChatSession(
                                            sessionId: activeSession.id,
                                            content: content
                                        )
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

                            // Summary Section (artifact, editorial v1, bulleted v1, interleaved v2, interleaved v1, or structured)
                            if let longformArtifact = content.longformArtifact {
                                LongformArtifactView(artifact: longformArtifact, contentId: content.id)
                                    .padding(.horizontal, DetailDesign.horizontalPadding)
                                    .padding(.top, DetailDesign.summaryTopPadding)
                                    .onAppear {
                                        logSummarySection(
                                            content: content,
                                            section: "longform_artifact",
                                            bulletPointCount: longformArtifact.artifact.payload.keyPoints.count,
                                            insightCount: 0
                                        )
                                    }
                            } else if let editorialSummary = content.editorialSummary {
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
                                StructuredSummaryView(
                                    summary: structuredSummary,
                                    contentId: content.id,
                                    startTopicSession: { topic in
                                        if content.contentType == .news {
                                            return try await ChatService.shared.startNewsTopicChat(
                                                newsItemId: content.id,
                                                topic: topic
                                            )
                                        }
                                        return try await ChatService.shared.startTopicChat(
                                            contentId: content.id,
                                            topic: topic
                                        )
                                    }
                                )
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

                            if let sourceMetadata = content.sourceMetadata {
                                SourceMetadataSection(
                                    metadata: sourceMetadata,
                                    openURL: openInAppBrowser
                                )
                                    .padding(.horizontal, DetailDesign.horizontalPadding)
                                    .padding(.top, DetailDesign.sectionSpacing)
                            }

                            let relevantLinks = content.relevantLinks
                            if content.contentType != .news, !relevantLinks.isEmpty {
                                relevantLinksSection(links: relevantLinks)
                                    .padding(.horizontal, DetailDesign.horizontalPadding)
                                    .padding(.top, DetailDesign.sectionSpacing)
                            }

                            if content.contentType == .news {
                                if content.newsMetadata != nil {
                                    modernSectionPlain(isPadded: false) {
                                        NewsItemDetailView(
                                            content: content
                                        )
                                    }
                                    .padding(.horizontal, DetailDesign.horizontalPadding)
                                    .padding(.top, DetailDesign.sectionSpacing)
                                } else {
                                    modernSectionPlain(isPadded: false) {
                                        VStack(alignment: .leading, spacing: 16) {
                                            ReaderSectionHeader("News Updates")
                                            Text("No news metadata available.")
                                                .font(.appSubheadline)
                                                .foregroundColor(Color.onSurfaceSecondary)
                                        }
                                    }
                                    .padding(.horizontal, DetailDesign.horizontalPadding)
                                    .padding(.top, DetailDesign.sectionSpacing)
                                }
                            }

                            if let discussion = inlineDiscussionSummaryPayload(for: content) {
                                communityDiscussionSummarySection(discussion: discussion, content: content)
                                    .padding(.horizontal, DetailDesign.horizontalPadding)
                                    .padding(.top, 16)
                            }

                            if content.contentType == .news, !relevantLinks.isEmpty {
                                relevantLinksSection(links: relevantLinks)
                                    .padding(.horizontal, DetailDesign.horizontalPadding)
                                    .padding(.top, DetailDesign.sectionSpacing)
                            }

                            // Full Content Section (collapsible, modern style)
                            if content.contentType != .news, let bodyText = viewModel.contentBody?.text {
                                modernExpandableSection(
                                    title: content.contentType == .podcast ? "Transcript" : "Full Article",
                                    icon: content.contentType == .podcast ? "text.alignleft" : "doc.text",
                                    isExpanded: $isTranscriptExpanded
                                ) {
                                    detailMarkdownBody(bodyText, content: content)
                                }
                                .padding(.horizontal, DetailDesign.horizontalPadding)
                                .padding(.top, DetailDesign.sectionSpacing)
                            } else if content.contentType == .podcast, let podcastMetadata = content.podcastMetadata, let transcript = podcastMetadata.transcript {
                                modernExpandableSection(
                                    title: "Transcript",
                                    icon: "text.alignleft",
                                    isExpanded: $isTranscriptExpanded
                                ) {
                                    detailMarkdownBody(transcript, content: content)
                                }
                                .padding(.horizontal, DetailDesign.horizontalPadding)
                                .padding(.top, DetailDesign.sectionSpacing)
                            } else if let fullMarkdown = content.fullMarkdown {
                                modernExpandableSection(
                                    title: content.contentType == .podcast ? "Transcript" : "Full Article",
                                    icon: "doc.text",
                                    isExpanded: $isTranscriptExpanded
                                ) {
                                    detailMarkdownBody(fullMarkdown, content: content)
                                }
                                .padding(.horizontal, DetailDesign.horizontalPadding)
                                .padding(.top, DetailDesign.sectionSpacing)
                            }

                            // Bottom spacing
                            Spacer()
                                .frame(height: 40)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    } else {
                        LoadingView()
                            .frame(minHeight: 400)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .coordinateSpace(name: "detailScroll")
            .scrollClipDisabled()
            .textSelection(.enabled)
            .accessibilityIdentifier("content.detail.screen")
            .overlay(alignment: .topLeading) {
                GeometryReader { proxy in
                    VStack(alignment: .leading, spacing: 0) {
                        floatingBackButton
                            .padding(.leading, 16)
                            .padding(.top, floatingBackTopPadding(for: proxy))
                        Spacer(minLength: 0)
                    }
                }
            }
        }
        .ignoresSafeArea(edges: hasHeroImage ? .top : [])
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(true)
        .toolbar(.hidden, for: .navigationBar)
        // Hide the main tab bar while viewing details
        .toolbar(.hidden, for: .tabBar)
        .onAppear {
            detailLogger.info(
                "[DetailNavigation] appear surface=\(navigationSurfaceName, privacy: .public) contentId=\(contentIdLogValue(at: currentIndex), privacy: .public) index=\(currentIndex, privacy: .public) idsCount=\(allContentIds.count, privacy: .public)"
            )
        }
        .task(id: currentIndex) {
            guard let idToLoad = contentId(at: currentIndex, context: "load") else { return }
            viewModel.updateContentId(idToLoad, contentType: initialContentType)
            await viewModel.loadContent()
        }
        .onChange(of: viewModel.content?.id) { oldValue, newValue in
            if let oldValue,
               oldValue != newValue,
               let oldTarget = podcastAudioTarget(forContentId: oldValue),
               narrationPlaybackService.speakingTarget == oldTarget {
                narrationPlaybackService.stop()
            }
            guard let id = newValue, let content = viewModel.content else {
                resetDiscussionState()
                return
            }
            resetDiscussionState(fallbackURL: discussionURL(for: content))
            readingStateStore.setCurrent(contentId: id, type: content.contentType)
            logSummarySnapshot(content: content, context: "content_change")
            if content.contentType == .news {
                Task { await prefetchStoredDiscussion(for: content) }
            }
        }
        // If user is navigating (chevrons or swipe), skip items that were already read.
        // Keyed on the loaded content id rather than `wasAlreadyReadWhenLoaded`: two
        // consecutive already-read podcasts leave the read flag true->true, which emits
        // no onChange, so the cascade would otherwise stall on the first read item.
        .onChange(of: viewModel.content?.id) { _, newId in
            guard newId != nil else { return }
            guard didTriggerNavigation, viewModel.content?.contentType == .podcast else { return }
            if viewModel.wasAlreadyReadWhenLoaded {
                let nextIndex = currentIndex + navigationDirection
                guard nextIndex >= 0 && nextIndex < allContentIds.count else {
                    // Reached the end; stop skipping further
                    didTriggerNavigation = false
                    navigationDirection = 0
                    return
                }
                currentIndex = nextIndex
                // Keep didTriggerNavigation/navigationDirection to allow cascading skips
            } else {
                // Landed on an unread item; reset navigation flags
                didTriggerNavigation = false
                navigationDirection = 0
            }
        }
        .onDisappear {
            detailLogger.info(
                "[DetailNavigation] disappear surface=\(navigationSurfaceName, privacy: .public) contentId=\(contentIdLogValue(at: currentIndex), privacy: .public) index=\(currentIndex, privacy: .public)"
            )
            if let content = viewModel.content,
               let target = podcastAudioTarget(for: content),
               narrationPlaybackService.speakingTarget == target {
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
            presentPendingShareIfNeeded()
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
                    .presentationDetents(discussionPresentationDetents)
                    .presentationDragIndicator(.visible)

            case .chat:
                if let content = viewModel.content {
                    chatSheet(content: content)
                        .presentationDetents([.medium, .large])
                        .presentationDragIndicator(.hidden)
                        .presentationCornerRadius(24)
                }
            }
        }
        .sheet(isPresented: $showLearningDeckCreateSheet) {
            if let content = viewModel.content {
                LearningDeckContentCreateSheet(
                    content: content,
                    onOpenDeck: { deck, url in
                        activeLearningDeckReader = LearningDeckReaderDestination(
                            deck: deck,
                            url: url
                        )
                    },
                    onNotice: { title, message in
                        activeAlert = ViewAlert(title: title, message: message)
                    }
                )
            }
        }
        .fullScreenCover(item: $activeReaderContent) { content in
            ArticleReaderView(
                content: content,
                articleBody: viewModel.readerBody,
                isLoading: viewModel.isLoadingReaderBody,
                errorMessage: viewModel.readerErrorMessage,
                onRetry: {
                    Task { await viewModel.loadReaderBody(for: content, force: true) }
                },
                onDigDeeper: { selectedText in
                    activeReaderContent = nil
                    handleReaderDigDeeper(selectedText: selectedText, content: content)
                }
            )
            .task(id: content.id) {
                await viewModel.loadReaderBody(for: content)
            }
        }
        .fullScreenCover(item: $activeBrowserDestination) { destination in
            SafariView(url: destination.url)
                .ignoresSafeArea()
        }
        .fullScreenCover(item: $activeLearningDeckReader) { destination in
            LearningDeckReaderView(
                deck: destination.deck,
                viewerURL: destination.url,
                onClose: {
                    activeLearningDeckReader = nil
                }
            )
            .ignoresSafeArea()
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
        content: ContentDetail,
        provider: ChatModelProvider = .openai,
        prompt: String? = nil
    ) async {
        guard !isStartingChat else { return }

        isStartingChat = true
        chatError = nil

        do {
            if content.contentType == .news {
                if let prompt, !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    let response = try await ChatService.shared.createAssistantTurn(
                        message: prompt,
                        screenContext: newsScreenContext(for: content)
                    )
                    activeSheet = nil
                    ChatNavigationCoordinator.shared.openAssistantTurn(response)
                } else {
                    let session = try await ChatService.shared.startNewsChat(
                        newsItemId: content.id,
                        provider: provider
                    )
                    activeSheet = nil
                    openChatSession(
                        sessionId: session.id,
                        content: content,
                        focusComposerOnAppear: true
                    )
                }
            } else {
                if let prompt, !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    let response = try await ChatService.shared.createAssistantTurn(
                        message: prompt,
                        screenContext: articleScreenContext(for: content)
                    )
                    activeSheet = nil
                    ChatNavigationCoordinator.shared.openAssistantTurn(response)
                } else {
                    let session = try await ChatService.shared.startArticleChat(
                        contentId: content.id,
                        provider: provider
                    )
                    activeSheet = nil
                    openChatSession(
                        sessionId: session.id,
                        content: content,
                        focusComposerOnAppear: true
                    )
                }
            }
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            chatError = error.localizedDescription
        }

        isStartingChat = false
    }

    private func articleScreenContext(for content: ContentDetail) -> AssistantScreenContext {
        AssistantScreenContext(
            screenType: "content_detail",
            screenTitle: content.displayTitle,
            contentId: content.id,
            visibleContentIds: allContentIds,
            selectedTopic: content.displayTitle,
            note: "The user is viewing an article or podcast detail. Use the linked content and reader context as primary context."
        )
    }

    private func startCouncilWithPrompt(
        _ prompt: String,
        content: ContentDetail,
        provider: ChatModelProvider = .openai
    ) async {
        guard !isStartingChat else { return }

        isStartingChat = true
        chatError = nil

        do {
            let session: ChatSessionSummary
            if content.contentType == .news {
                if let existingSession = try await ChatService.shared.getSessionForNewsItem(
                    newsItemId: content.id
                ) {
                    session = existingSession
                } else {
                    session = try await ChatService.shared.startNewsChat(
                        newsItemId: content.id,
                        provider: provider
                    )
                }
            } else {
                if let existingSession = try await ChatService.shared.getSessionForContent(
                    contentId: content.id
                ) {
                    session = existingSession
                } else {
                    session = try await ChatService.shared.startArticleChat(
                        contentId: content.id,
                        provider: provider
                    )
                }
            }
            activeSheet = nil
            openChatSession(
                sessionId: session.id,
                content: content,
                pendingCouncilPrompt: session.isCouncilMode ? nil : prompt
            )
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            chatError = error.localizedDescription
        }

        isStartingChat = false
    }

    private func deepDivePrompt(for content: ContentDetail) -> String {
        "Dig deeper into the key points of \(content.displayTitle). For each main point, explain reasoning, supporting evidence, and include a bit more detail explaining the point. Also pull out key ideas from the discussion context when available, and add more insights from the discussion, including notable agreements and disagreements. Keep answers concise and numbered."
    }

    private func handleReaderDigDeeper(selectedText: String, content: ContentDetail) {
        let trimmed = selectedText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        Task { @MainActor in
            do {
                let isNews = content.contentType == .news
                let response = try await ChatService.shared.createAssistantTurn(
                    message: "Dig deeper into this selected text from \(content.displayTitle): \"\(trimmed)\"",
                    screenContext: AssistantScreenContext(
                        screenType: "article_reader",
                        screenTitle: "Article Reader",
                        contentId: isNews ? nil : content.id,
                        newsItemId: isNews ? content.id : nil,
                        visibleContentIds: isNews ? [] : allContentIds,
                        visibleNewsItemIds: isNews ? allContentIds : [],
                        selectedTopic: trimmed,
                        query: trimmed,
                        note: "The user selected text from the full article reader. Use the article body and selected passage as primary context. For news items, use news_item_id and do not resolve same-numbered content IDs."
                    )
                )
                ChatNavigationCoordinator.shared.openAssistantTurn(response)
            } catch where isNetworkCancellation(error) {
                return
            } catch {
                ToastService.shared.showError("Failed to dig deeper: \(error.localizedDescription)")
            }
        }
    }

    private func councilPrompt(for content: ContentDetail) -> String {
        "Give me your perspective on \(content.displayTitle). Keep it short: 2-4 concise bullets on what matters most, what is weak or missing, and what actions or implications follow."
    }

    private func deepResearchPrompt(for content: ContentDetail) -> String {
        "Conduct comprehensive research on \(content.displayTitle). Find additional sources, verify claims, identify related developments, and provide a thorough analysis with citations."
    }

    private func startDeepResearchWithPrompt(_ prompt: String, content: ContentDetail) async {
        guard !isStartingChat else { return }

        isStartingChat = true
        chatError = nil

        do {
            let isNews = content.contentType == .news
            let session = try await ChatService.shared.startDeepResearch(
                contentId: isNews ? nil : content.id,
                newsItemId: isNews ? content.id : nil
            )
            let pendingResponse = try await ChatService.shared.sendMessageAsync(
                sessionId: session.id,
                message: prompt
            )

            activeSheet = nil
            openChatSession(
                sessionId: session.id,
                content: content,
                initialUserMessage: pendingResponse.userMessage,
                pendingMessageId: pendingResponse.messageId
            )
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            chatError = error.localizedDescription
        }

        isStartingChat = false
    }

    private func newsScreenContext(for content: ContentDetail) -> AssistantScreenContext {
        AssistantScreenContext(
            screenType: "content_detail",
            screenTitle: content.displayTitle,
            newsItemId: content.id,
            visibleNewsItemIds: allContentIds,
            selectedTopic: content.displayTitle,
            note: "The user is viewing a news item detail. Use the news item snapshot; do not resolve same-numbered content IDs."
        )
    }

    private func activeChatSession(for content: ContentDetail) -> ActiveChatSession? {
        if content.contentType == .news {
            return chatSessionManager.getSession(forNewsItemId: content.id)
        }
        return chatSessionManager.getSession(forContentId: content.id)
    }

    @MainActor
    private func openChatSession(
        sessionId: Int,
        content: ContentDetail,
        initialUserMessage: ChatMessage? = nil,
        pendingMessageId: Int? = nil,
        pendingCouncilPrompt: String? = nil,
        focusComposerOnAppear: Bool = false
    ) {
        let isNews = content.contentType == .news
        openChatSession(
            sessionId: sessionId,
            contentId: isNews ? nil : content.id,
            newsItemId: isNews ? content.id : nil,
            initialUserMessage: initialUserMessage,
            pendingMessageId: pendingMessageId,
            pendingCouncilPrompt: pendingCouncilPrompt,
            focusComposerOnAppear: focusComposerOnAppear
        )
    }

    @MainActor
    private func openChatSession(
        sessionId: Int,
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        initialUserMessage: ChatMessage? = nil,
        pendingMessageId: Int? = nil,
        pendingCouncilPrompt: String? = nil,
        focusComposerOnAppear: Bool = false
    ) {
        chatSessionManager.stopTracking(sessionId: sessionId)
        ChatNavigationCoordinator.shared.open(
            ChatSessionRoute(
                sessionId: sessionId,
                contentId: contentId,
                newsItemId: newsItemId,
                initialUserMessageText: initialUserMessage?.content,
                initialUserMessageTimestamp: initialUserMessage?.timestamp,
                pendingMessageId: pendingMessageId,
                pendingCouncilPrompt: pendingCouncilPrompt,
                focusComposerOnAppear: focusComposerOnAppear
            )
        )
    }

    @ViewBuilder
    private func audioPromptCard(for content: ContentDetail) -> some View {
        NarrationPressButton(
            isDisabled: isPodcastAudioLoading(for: content),
            accessibilityLabel: podcastAudioAccessibilityLabel(for: content),
            onTap: {
                Task { await handlePodcastAudio(for: content) }
            },
            onSelectPlaybackSpeed: { option in
                Task {
                    await handlePodcastAudio(
                        for: content,
                        rate: option.rate
                    )
                }
            }
        ) {
            HStack(spacing: 12) {
                chatSheetIcon(
                    isPodcastAudioActive(for: content)
                        ? "pause.fill"
                        : "person.3.sequence.fill",
                    color: .readerBodyText
                )

                VStack(alignment: .leading, spacing: 3) {
                    Text(
                        isPodcastAudioActive(for: content)
                            ? "Pause podcast overview"
                            : "Podcast overview"
                    )
                    .font(.appSubheadline)
                    .fontWeight(.semibold)
                    .foregroundColor(Color.onSurface)
                    Text(podcastAudioStatusText(for: content))
                        .font(.appCaption)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.85)
                }

                Spacer()

                if isPodcastAudioLoading(for: content) {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Image(systemName: "chevron.right")
                        .font(.appCaption.weight(.semibold))
                        .foregroundColor(Color.onSurfaceTertiary)
                }
            }
            .chatWideActionSurface()
        }
        .accessibilityIdentifier("content.audio.podcast_overview")
    }

    private func supportsPodcastAudio(for content: ContentDetail) -> Bool {
        content.contentType == .article || content.contentType == .news || content.contentType == .podcast
    }

    private func podcastAudioTarget(for content: ContentDetail) -> NarrationTarget? {
        podcastAudioTarget(forContentId: content.id)
    }

    private func podcastAudioTarget(forContentId contentId: Int) -> NarrationTarget? {
        guard let episode = audioEpisodeByContentId[contentId] else { return nil }
        return .audioEpisode(episode.id)
    }

    @ViewBuilder
    private func podcastAudioActionIcon(for content: ContentDetail, overlaid: Bool = false) -> some View {
        if isPodcastAudioLoading(for: content) {
            ProgressView()
                .scaleEffect(0.8)
                .frame(width: 44, height: 44)
        } else if isPodcastAudioActive(for: content) {
            minimalActionIcon("speaker.wave.3.fill", color: .readerBodyText, overlaid: overlaid)
        } else {
            minimalActionIcon("speaker.wave.2", overlaid: overlaid)
        }
    }

    private func podcastAudioAccessibilityLabel(for content: ContentDetail) -> String {
        if isPodcastAudioActive(for: content) {
            return "Pause podcast overview"
        }
        return "Play podcast overview at \(narrationPlaybackService.playbackSpeedTitle)"
    }

    private func isPodcastAudioActive(for content: ContentDetail) -> Bool {
        guard let target = podcastAudioTarget(for: content) else { return false }
        return narrationPlaybackService.isSpeaking
            && narrationPlaybackService.speakingTarget == target
    }

    private func isPodcastAudioLoading(for content: ContentDetail) -> Bool {
        loadingAudioEpisodeContentIds.contains(content.id)
    }

    private func shouldShowPodcastPlaybackControls(for content: ContentDetail) -> Bool {
        if isPodcastAudioLoading(for: content) {
            return true
        }
        guard let target = podcastAudioTarget(for: content) else { return false }
        return narrationPlaybackService.speakingTarget == target
    }

    private func podcastPlaybackControls(for content: ContentDetail) -> some View {
        NarrationPlaybackControlRow(
            playbackService: narrationPlaybackService,
            target: podcastAudioTarget(for: content),
            isPreparing: isPodcastAudioLoading(for: content),
            onTogglePlayback: {
                Task { await handlePodcastAudio(for: content) }
            }
        )
        .accessibilityIdentifier("content.audio.podcast.controls")
    }

    private func podcastAudioStatusText(for content: ContentDetail) -> String {
        if isPodcastAudioLoading(for: content) {
            return "Preparing audio"
        }
        if isPodcastAudioActive(for: content) {
            return "Playing at \(narrationPlaybackService.playbackSpeedTitle)"
        }
        return "1-minute discussion"
    }

    @MainActor
    private func handlePodcastAudio(
        for content: ContentDetail,
        rate: Float? = nil
    ) async {
        let startedAt = Date()
        let playbackRate = rate ?? narrationPlaybackService.playbackRate
        if isPodcastAudioActive(for: content) {
            if abs(narrationPlaybackService.playbackRate - playbackRate) < 0.001 {
                narrationPlaybackService.pause()
            } else {
                narrationPlaybackService.setPlaybackRate(playbackRate)
            }
            return
        }
        guard supportsPodcastAudio(for: content) else { return }
        guard !isPodcastAudioLoading(for: content) else { return }
        detailLogger.info(
            "[PodcastAudio] flow started | contentId=\(content.id) type=\(content.contentType.rawValue, privacy: .public) rate=\(playbackRate)"
        )

        loadingAudioEpisodeContentIds.insert(content.id)
        defer { loadingAudioEpisodeContentIds.remove(content.id) }

        do {
            let episode: AudioEpisode
            if let existingEpisode = audioEpisodeByContentId[content.id] {
                episode = existingEpisode
                detailLogger.info(
                    "[PodcastAudio] reusing episode | contentId=\(content.id) episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public)"
                )
            } else {
                episode = try await createPodcastAudioEpisode(for: content)
                detailLogger.info(
                    "[PodcastAudio] episode created | contentId=\(content.id) episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public) elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
                )
            }
            audioEpisodeByContentId[content.id] = episode
            guard viewModel.content?.id == content.id else { return }
            let target = NarrationTarget.audioEpisode(episode.id)
            try await narrationPlaybackService.playStreamingNarration(
                for: target,
                rate: playbackRate,
                fetchStreamResource: {
                    try await AudioEpisodeService.shared.streamResource(for: episode)
                }
            )
            detailLogger.info(
                "[PodcastAudio] playback requested | contentId=\(content.id) episodeId=\(episode.id) elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
            )
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            detailLogger.error(
                "[PodcastAudio] flow failed | contentId=\(content.id) elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) error=\(error.localizedDescription, privacy: .public)"
            )
            activeAlert = ViewAlert(
                title: "Audio",
                message: "Failed to load podcast audio: \(error.localizedDescription)"
            )
        }
    }

    private func createPodcastAudioEpisode(for content: ContentDetail) async throws -> AudioEpisode {
        switch content.contentType {
        case .article, .podcast, .insight_report, .unknown, .unknownRaw:
            return try await AudioEpisodeService.shared.createContentCouncilEpisode(
                contentId: content.id,
                delivery: .inline
            )
        case .news:
            return try await AudioEpisodeService.shared.createNewsItemDiscussionEpisode(
                newsItemId: content.id,
                delivery: .inline
            )
        }
    }

    // MARK: - Parallax Hero Header
    private func contentId(at index: Int, context: String) -> Int? {
        guard let contentId = navigationContext.contentId(at: index) else {
            detailLogger.error(
                "[DetailNavigation] invalidContentIndex context=\(context, privacy: .public) surface=\(navigationSurfaceName, privacy: .public) index=\(index, privacy: .public) idsCount=\(allContentIds.count, privacy: .public)"
            )
            return nil
        }
        return contentId
    }

    private func contentIdLogValue(at index: Int) -> String {
        guard let contentId = navigationContext.contentId(at: index) else { return "invalid" }
        return String(contentId)
    }

    private var hasHeroImage: Bool {
        guard let content = viewModel.content,
              let imageUrlString = content.imageUrl,
              !imageUrlString.isEmpty,
              content.contentType != .news,
              buildImageURL(from: imageUrlString) != nil else {
            return false
        }
        return true
    }

    @ViewBuilder
    private func heroHeader(content: ContentDetail) -> some View {
        if let imageUrlString = content.imageUrl,
           !imageUrlString.isEmpty,
           content.contentType != .news,
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
                            thumbnailUrl: thumbnailUrl,
                            targetSize: CGSize(width: geo.size.width, height: imageHeight)
                        ) { image in
                            image
                                .resizable()
                                .aspectRatio(contentMode: .fill)
                                .frame(width: geo.size.width, height: imageHeight)
                                .clipped()
                        } placeholder: {
                            Rectangle()
                                .fill(Color.surfaceTertiary)
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
                        .init(color: .clear, location: 0.20),
                        .init(color: Color.black.opacity(0.35), location: 0.45),
                        .init(color: Color.black.opacity(0.75), location: 0.72),
                        .init(color: Color.black.opacity(0.88), location: 0.90),
                        .init(color: Color.surfacePrimary, location: 1.0)
                    ]),
                    startPoint: .top,
                    endPoint: .bottom
                )
                .allowsHitTesting(false)

                // Layer 3: Title + metadata + action bar
                VStack(alignment: .leading, spacing: 8) {
                    Text(content.displayTitle)
                        .font(detailTitleFont)
                        .fontWeight(detailTitleWeight)
                        .foregroundColor(.white)
                        .shadow(color: .black.opacity(0.5), radius: 4, x: 0, y: 1)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("content.detail.title.\(content.id)")

                    HStack(spacing: 6) {
                        HStack(spacing: 4) {
                            Image(systemName: contentTypeIcon(for: content))
                                .font(.appCaption2)
                            Text(content.detailTypeLabel)
                                .font(.appCaption)
                                .fontWeight(.medium)
                        }
                        .foregroundColor(.white.opacity(0.9))

                        if let source = content.source {
                            Text("·")
                                .foregroundColor(.white.opacity(0.5))
                            Text(source)
                                .font(.appCaption)
                                .foregroundColor(.white.opacity(0.8))
                        }

                        Text("·")
                            .foregroundColor(.white.opacity(0.5))

                        ContentTimestampText(
                            rawValue: content.primaryTimestamp,
                            style: .detailMeta,
                            fallback: "Recent"
                        )
                        .font(.appCaption)
                        .foregroundColor(.white.opacity(0.8))
                    }
                    .shadow(color: .black.opacity(0.4), radius: 3, x: 0, y: 1)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(detailMetadataAccessibilityLabel(for: content))

                    actionBar(content: content, overlaid: true)
                        .padding(.top, 2)

                    if shouldShowPodcastPlaybackControls(for: content) {
                        podcastPlaybackControls(for: content)
                            .padding(.top, 2)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, DetailDesign.headerHorizontalPadding)
                .padding(.bottom, 10)
            }
            .frame(height: DetailDesign.parallaxHeroHeight)
            .mask(Rectangle().padding(.top, -200))
            .fullScreenCover(item: $selectedImageAsset) { asset in
                FullImageView(imageURL: asset.imageURL, thumbnailURL: asset.thumbnailURL)
            }
        } else {
            // No image fallback — standard layout
            VStack(alignment: .leading, spacing: 0) {
                Spacer()
                    .frame(height: textOnlyHeaderTopSpacer(for: content))

                VStack(alignment: .leading, spacing: 8) {
                    Text(content.displayTitle)
                        .font(detailTitleFont)
                        .fontWeight(detailTitleWeight)
                        .foregroundColor(Color.onSurface)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("content.detail.title.\(content.id)")

                    HStack(spacing: 6) {
                        HStack(spacing: 4) {
                            Image(systemName: contentTypeIcon(for: content))
                                .font(.appCaption2)
                            Text(content.detailTypeLabel)
                                .font(.appCaption)
                                .fontWeight(.medium)
                        }
                        .foregroundColor(.onSurfaceSecondary)

                        if let source = content.source {
                            Text("·")
                                .foregroundColor(Color.onSurfaceSecondary.opacity(0.4))
                            Text(source)
                                .font(.appCaption)
                                .foregroundColor(Color.onSurfaceSecondary)
                        }

                        Text("·")
                            .foregroundColor(Color.onSurfaceSecondary.opacity(0.4))

                        ContentTimestampText(
                            rawValue: content.primaryTimestamp,
                            style: .detailMeta,
                            fallback: "Recent"
                        )
                        .font(.appCaption)
                        .foregroundColor(Color.onSurfaceSecondary)
                    }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(detailMetadataAccessibilityLabel(for: content))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, DetailDesign.headerHorizontalPadding)
                .padding(.top, DetailDesign.textOnlyTitleTopPadding)
                .padding(.bottom, 6)

                actionBar(content: content, overlaid: false)
                    // Optical inset via reduced positive padding (not negative
                    // padding on the bar, which pushes the edge icons outside the
                    // hittable frame and makes them untappable).
                    .padding(.horizontal, DetailDesign.headerHorizontalPadding - DetailDesign.actionIconOpticalInset)
                    .padding(.top, 2)

                if shouldShowPodcastPlaybackControls(for: content) {
                    podcastPlaybackControls(for: content)
                        .padding(.horizontal, DetailDesign.headerHorizontalPadding)
                        .padding(.top, 4)
                }
            }
        }
    }

    private func textOnlyHeaderTopSpacer(for content: ContentDetail) -> CGFloat {
        content.contentType == .news
            ? DetailDesign.textOnlyNewsHeaderTopSpacer
            : DetailDesign.textOnlyStandardHeaderTopSpacer
    }

    private var detailTitleFont: Font {
        .appSerif(size: 20, relativeTo: .title3, weight: .medium)
    }

    private var detailTitleWeight: Font.Weight {
        .medium
    }

    private func detailMetadataAccessibilityLabel(for content: ContentDetail) -> String {
        [
            content.detailTypeLabel,
            content.source,
            ContentTimestampFormatter.text(from: content.primaryTimestamp, style: .detailMeta) ?? "Recent"
        ]
        .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
        .joined(separator: ", ")
    }

    private var floatingBackButton: some View {
        Button {
            detailLogger.info(
                "[DetailNavigation] backButtonTapped surface=\(navigationSurfaceName, privacy: .public) contentId=\(contentIdLogValue(at: currentIndex), privacy: .public) index=\(currentIndex, privacy: .public)"
            )
            dismiss()
        } label: {
            Image(systemName: "chevron.left")
                .font(.appSymbol(size: 20, weight: .semibold))
                .foregroundStyle(.white)
                .frame(
                    width: DetailDesign.floatingBackButtonSize,
                    height: DetailDesign.floatingBackButtonSize
                )
                .background(
                    Circle()
                        .fill(Color(red: 0.07, green: 0.06, blue: 0.05).opacity(0.42))
                )
                .overlay(
                    Circle()
                        .stroke(Color.white.opacity(0.22), lineWidth: 1)
                )
                .shadow(
                    color: Color(red: 0.05, green: 0.045, blue: 0.04).opacity(0.28),
                    radius: 12,
                    x: 0,
                    y: 8
                )
        }
        .buttonStyle(.plain)
        .textSelection(.disabled)
        .accessibilityLabel("Back")
    }

    private func floatingBackTopPadding(for proxy: GeometryProxy) -> CGFloat {
        if hasHeroImage {
            let fallbackTopInset: CGFloat = 56
            return max(proxy.safeAreaInsets.top, fallbackTopInset) + 8
        }

        return DetailDesign.textOnlyBackButtonTopPadding
    }

    @ViewBuilder
    private func heroPlaceholder(content: ContentDetail) -> some View {
        Rectangle()
            .fill(
                LinearGradient(
                    colors: [
                        Color.surfaceContainer,
                        Color.surfaceTertiary
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .frame(height: DetailDesign.heroHeight)
            .overlay(
                Image(systemName: contentTypeIcon(for: content))
                    .font(.appSymbol(size: 56, weight: .ultraLight))
                    .foregroundColor(.white.opacity(0.3))
            )
    }

    private func contentTypeIcon(for content: ContentDetail) -> String {
        switch content.contentType {
        case .article: return "doc.text"
        case .podcast: return "headphones"
        case .news: return "newspaper"
        case .insight_report, .unknown, .unknownRaw: return "doc.text"
        }
    }

    // MARK: - Modern Action Bar (Minimal, Twitter-inspired)
    @ViewBuilder
    private func actionBar(content: ContentDetail, overlaid: Bool = false) -> some View {
        HStack(spacing: 0) {
            // Primary action - Open in browser
            if let url = URL(string: content.url) {
                Button {
                    openInAppBrowser(url)
                } label: {
                    minimalActionIcon("safari", overlaid: overlaid)
                }
                .buttonStyle(.plain)
                .detailActionBarSegment()
                .accessibilityIdentifier("content.action.open_external")
                .accessibilityLabel("Open article")
            }

            if let discussionURL = discussionURL(for: content) {
                Button {
                    handleDiscussionTap(content: content, fallbackURL: discussionURL)
                } label: {
                    minimalActionIcon("bubble.left.and.bubble.right", overlaid: overlaid)
                }
                .detailActionBarSegment()
                .accessibilityIdentifier("content.discussion.open")
                .accessibilityLabel("Open comments")
            }

            // Share
            Button(action: { activeSheet = .share }) {
                minimalActionIcon("square.and.arrow.up", overlaid: overlaid)
            }
            .detailActionBarSegment()
            .accessibilityIdentifier("content.action.share")

            if viewModel.canShowReader(for: content) {
                Button {
                    activeReaderContent = content
                } label: {
                    if viewModel.isLoadingReaderBody {
                        ProgressView()
                            .scaleEffect(0.8)
                            .frame(width: 44, height: 44)
                    } else {
                        minimalActionIcon("doc.richtext", overlaid: overlaid)
                    }
                }
                .detailActionBarSegment()
                .accessibilityIdentifier("content.action.reader")
                .accessibilityLabel("Read full article")
            }

            // Download more from series (article/podcast only)
            if content.contentType == .article || content.contentType == .podcast {
                Button { activeSheet = .download } label: {
                    minimalActionIcon("tray.and.arrow.down", overlaid: overlaid)
                }
                .detailActionBarSegment()
                .accessibilityIdentifier("content.action.download_more")
                .accessibilityLabel("Download more from this series")
            }

            // Save linked article (news only)
            if content.contentType == .news {
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
                        knowledgeActionIcon(isSaved: false, overlaid: overlaid)
                    }
                }
                .disabled(isConverting)
                .detailActionBarSegment()
                .accessibilityIdentifier("content.action.convert")
                .accessibilityLabel("Save linked article to Knowledge")
            }

            if content.contentType != .news {
                Button(action: {
                    Task { await viewModel.toggleKnowledgeSave() }
                }) {
                    knowledgeActionIcon(isSaved: content.isSavedToKnowledge, overlaid: overlaid)
                }
                .detailActionBarSegment()
                .accessibilityIdentifier("content.action.knowledge")
                .accessibilityLabel(content.isSavedToKnowledge ? "Remove from Knowledge" : "Save to Knowledge")
            }

            if supportsPodcastAudio(for: content) {
                NarrationPressButton(
                    isDisabled: isPodcastAudioLoading(for: content),
                    accessibilityLabel: podcastAudioAccessibilityLabel(for: content),
                    onTap: {
                        Task { await handlePodcastAudio(for: content) }
                    },
                    onSelectPlaybackSpeed: { option in
                        Task {
                            await handlePodcastAudio(
                                for: content,
                                rate: option.rate
                            )
                        }
                    }
                ) {
                    podcastAudioActionIcon(for: content, overlaid: overlaid)
                }
                .detailActionBarSegment()
                .accessibilityIdentifier("content.action.podcast_audio")
            }

            Button {
                showLearningDeckCreateSheet = true
            } label: {
                minimalActionIcon("rectangle.stack", overlaid: overlaid)
                    .symbolEffect(.bounce, value: learningDeckHintBounce)
            }
            .detailActionBarSegment()
            .accessibilityIdentifier("content.action.learning_deck")
            .accessibilityLabel("Create Learning Deck")
            .popover(isPresented: $showLearningDeckHint) {
                LearningDeckEntryHint()
                    .presentationCompactAdaptation(.popover)
            }
            .task {
                guard !E2ETestLaunch.isEnabled else { return }
                guard !hasSeenLearningDeckHint else { return }
                try? await Task.sleep(nanoseconds: 600_000_000)
                guard !Task.isCancelled else { return }
                hasSeenLearningDeckHint = true
                learningDeckHintBounce.toggle()
                showLearningDeckHint = true
            }

            // Deep Dive chat
            Button(action: {
                Task {
                    if let activeSession = activeChatSession(for: content) {
                        openChatSession(
                            sessionId: activeSession.id,
                            content: content
                        )
                        return
                    }
                    await handleChatButtonTapped()
                }
            }) {
                if isStartingChat {
                    Image(systemName: "brain.head.profile")
                        .font(.appSymbol(size: 20, weight: .regular))
                        .foregroundColor(overlaid ? .white : .readerBodyText)
                        .shadow(color: overlaid ? .black.opacity(0.4) : .clear, radius: 3, x: 0, y: 1)
                        .frame(width: 44, height: 44)
                        .symbolEffect(.pulse, options: .repeating)
                } else {
                    minimalActionIcon("brain.head.profile", overlaid: overlaid)
                }
            }
            .disabled(isCheckingChatSession)
            .detailActionBarSegment()
            .accessibilityIdentifier("content.action.deep_dive")
            .accessibilityLabel("Start deep dive")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(height: 44)
        .textSelection(.disabled)
    }

    @ViewBuilder
    private func minimalActionIcon(_ icon: String, color: Color = .readerBodyText, overlaid: Bool = false) -> some View {
        Image(systemName: icon)
            .font(.appSymbol(size: 20, weight: .regular))
            .foregroundColor(overlaid ? .white : color)
            .shadow(color: overlaid ? .black.opacity(0.4) : .clear, radius: 3, x: 0, y: 1)
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
    }

    @ViewBuilder
    private func knowledgeActionIcon(isSaved: Bool, overlaid: Bool) -> some View {
        let iconColor: Color = overlaid ? .white : .readerBodyText
        KnowledgeSaveIcon(
            isSaved: isSaved,
            savedColor: iconColor,
            unsavedColor: iconColor,
            badgeColor: iconColor,
            badgeForegroundColor: .surfacePrimary
        )
        .shadow(color: overlaid ? .black.opacity(0.4) : .clear, radius: 3, x: 0, y: 1)
        .frame(width: 44, height: 44)
        .contentShape(Rectangle())
    }

    // MARK: - Mini Sheet Components

    @ViewBuilder
    private func sheetHeader(title: String? = nil, dismiss: @escaping () -> Void) -> some View {
        let hasTitle = title != nil

        VStack(spacing: 0) {
            RoundedRectangle(cornerRadius: 2.5)
                .fill(Color.outlineVariant.opacity(hasTitle ? 0.3 : 0.38))
                .frame(width: hasTitle ? 36 : 38, height: 5)
                .padding(.top, 8)

            HStack {
                if let title {
                    Text(title)
                        .font(.appTitle3)
                }
                Spacer()
                Button(action: dismiss) {
                    Image(systemName: "xmark")
                        .font(.appBody)
                        .fontWeight(.semibold)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .frame(width: 44, height: 44)
                        .background(Color.surfaceTertiary)
                        .clipShape(Circle())
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Close sheet")
                .accessibilityIdentifier("content.sheet.close")
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.top, hasTitle ? 14 : 10)
            .padding(.bottom, hasTitle ? 16 : 10)
        }
    }

    @ViewBuilder
    private func sheetOptionRow(
        icon: String,
        iconColor: Color = .readerBodyText,
        title: String,
        subtitle: String,
        badge: String? = nil,
        disabled: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                sheetOptionIcon(icon, color: iconColor)

                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(.appSubheadline)
                        .fontWeight(.semibold)
                        .foregroundColor(Color.onSurface)
                    Text(subtitle)
                        .font(.appCaption)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.88)
                }

                Spacer()

                if let badge {
                    Text(badge)
                        .font(.appCaption2)
                        .fontWeight(.semibold)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 4)
                        .background(Color.surfaceTertiary)
                        .clipShape(Capsule())
                }
            }
            .miniSheetOptionSurface()
        }
        .buttonStyle(SheetOptionButtonStyle())
        .disabled(disabled)
        .opacity(disabled ? 0.55 : 1)
    }

    private func sheetOptionIcon(
        _ icon: String,
        color: Color,
        size: CGFloat = 17
    ) -> some View {
        Image(systemName: icon)
            .font(.appSymbol(size: size, weight: .semibold))
            .foregroundColor(color)
            .frame(width: 34, height: 34)
            .background(color.opacity(0.13))
            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
    }

    private var chatTileColumns: [GridItem] {
        [
            GridItem(.flexible(), spacing: 10),
            GridItem(.flexible(), spacing: 10)
        ]
    }

    @ViewBuilder
    private func chatActionTile(
        icon: String,
        title: String,
        badge: String? = nil,
        disabled: Bool = false,
        accessibilityIdentifier: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 8) {
                    chatSheetIcon(icon, color: .readerBodyText)

                    Spacer(minLength: 0)

                    if let badge {
                        Text(badge)
                            .font(.appCaption2.weight(.semibold).monospacedDigit())
                            .foregroundColor(Color.onSurfaceSecondary)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 4)
                            .background(Color.surfaceTertiary.opacity(0.82))
                            .clipShape(Capsule())
                    }
                }

                Spacer(minLength: 0)

                Text(title)
                    .font(.appSubheadline.weight(.semibold))
                    .foregroundColor(Color.onSurface)
                    .lineLimit(2)
                    .minimumScaleFactor(0.84)
                    .multilineTextAlignment(.leading)
            }
            .chatTileSurface()
        }
        .buttonStyle(ChatSheetButtonStyle())
        .disabled(disabled)
        .opacity(disabled ? 0.55 : 1)
        .accessibilityLabel(badge.map { "\(title), \($0)" } ?? title)
        .accessibilityIdentifier(accessibilityIdentifier)
    }

    private func chatSheetIcon(
        _ icon: String,
        color: Color
    ) -> some View {
        Image(systemName: icon)
            .font(.appSymbol(size: 18, weight: .semibold))
            .foregroundColor(color)
            .frame(width: 42, height: 42)
            .background(color.opacity(0.15))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
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
                        queueShareContent(.light)
                    }
                )
                sheetOptionRow(
                    icon: "text.quote",
                    title: "Key points",
                    subtitle: "Summary, top quotes, and link",
                    action: {
                        queueShareContent(.medium)
                    }
                )
                sheetOptionRow(
                    icon: "doc.plaintext",
                    title: "Full content",
                    subtitle: "Complete article or transcript",
                    action: {
                        queueShareContent(.full)
                    }
                )
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)

            Divider()
                .padding(.horizontal, Spacing.appHorizontalMargin)
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
            .padding(.horizontal, Spacing.appHorizontalMargin)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.surfacePrimary)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("content.share.sheet")
    }

    private func queueShareContent(_ option: ShareContentOption) {
        pendingShareOption = option
        activeSheet = nil
    }

    private func presentPendingShareIfNeeded() {
        guard let option = pendingShareOption else { return }
        pendingShareOption = nil

        DispatchQueue.main.async {
            viewModel.shareContent(option: option)
        }
    }

    // MARK: - Download Sheet
    @ViewBuilder
    private var downloadSheet: some View {
        VStack(spacing: 0) {
            sheetHeader(title: "Load more from series") { activeSheet = nil }

            VStack(spacing: 8) {
                sheetOptionRow(
                    icon: "square.stack",
                    iconColor: .readerBodyText,
                    title: "3 episodes",
                    subtitle: "Quick catch-up",
                    action: {
                        activeSheet = nil
                        Task { await viewModel.downloadMoreFromSeries(count: 3) }
                    }
                )
                sheetOptionRow(
                    icon: "square.stack",
                    iconColor: .readerBodyText,
                    title: "5 episodes",
                    subtitle: "Recent backlog",
                    action: {
                        activeSheet = nil
                        Task { await viewModel.downloadMoreFromSeries(count: 5) }
                    }
                )
                sheetOptionRow(
                    icon: "square.stack.3d.up",
                    iconColor: .readerBodyText,
                    title: "10 episodes",
                    subtitle: "Deep dive into the series",
                    action: {
                        activeSheet = nil
                        Task { await viewModel.downloadMoreFromSeries(count: 10) }
                    }
                )
                sheetOptionRow(
                    icon: "square.stack.3d.up.fill",
                    iconColor: .readerBodyText,
                    title: "20 episodes",
                    subtitle: "Full archive pull",
                    action: {
                        activeSheet = nil
                        Task { await viewModel.downloadMoreFromSeries(count: 20) }
                    }
                )
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.surfacePrimary)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("content.download.sheet")
    }

    // MARK: - AI Chat Sheet
    @ViewBuilder
    private func chatSheet(content: ContentDetail) -> some View {
        VStack(spacing: 0) {
            sheetHeader { activeSheet = nil }

            ScrollView {
                VStack(spacing: 12) {
                    if let chatError {
                        HStack(spacing: 8) {
                            Image(systemName: "exclamationmark.circle.fill")
                                .foregroundColor(.statusDestructive)
                            Text(chatError)
                                .font(.appFootnote)
                                .foregroundColor(.statusDestructive)
                        }
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.statusDestructive.opacity(0.1))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                    }

                    LazyVGrid(columns: chatTileColumns, spacing: 10) {
                        chatActionTile(
                            icon: "message",
                            title: "Start chat",
                            disabled: isStartingChat,
                            accessibilityIdentifier: "content.chat.start"
                        ) {
                            Task {
                                await startChat(
                                    content: content,
                                    provider: .openai
                                )
                            }
                        }

                        chatActionTile(
                            icon: "doc.text.magnifyingglass",
                            title: "Dig deeper",
                            disabled: isStartingChat,
                            accessibilityIdentifier: "content.chat.dig_deeper"
                        ) {
                            Task {
                                await startChat(
                                    content: content,
                                    provider: .openai,
                                    prompt: deepDivePrompt(for: content)
                                )
                            }
                        }

                        chatActionTile(
                            icon: "person.3.sequence.fill",
                            title: "Council Chat",
                            disabled: isStartingChat,
                            accessibilityIdentifier: "content.chat.council"
                        ) {
                            Task {
                                await startCouncilWithPrompt(
                                    councilPrompt(for: content),
                                    content: content,
                                    provider: .openai
                                )
                            }
                        }

                        chatActionTile(
                            icon: "magnifyingglass.circle.fill",
                            title: "Deep Research",
                            badge: "2-5 min",
                            disabled: isStartingChat,
                            accessibilityIdentifier: "content.chat.deep_research"
                        ) {
                            Task { await startDeepResearchWithPrompt(deepResearchPrompt(for: content), content: content) }
                        }
                    }

                    if supportsPodcastAudio(for: content) {
                        audioPromptCard(for: content)
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)

                Color.clear.frame(height: 16)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.surfacePrimary)
        .accessibilityLabel("Chat actions")
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("content.chat.sheet")
    }

    private var discussionPresentationDetents: Set<PresentationDetent> {
        guard !isLoadingDiscussion,
              discussionPayload?.hasRenderableContent == true else {
            return [.height(250)]
        }
        if let discussionPayload,
           usesCompactDiscussionDetent(discussionPayload) {
            return [.height(compactDiscussionDetentHeight(for: discussionPayload))]
        }
        return [.medium, .large]
    }

    private func usesCompactDiscussionDetent(_ discussion: ContentDiscussion) -> Bool {
        discussion.mode == "comments"
            && discussion.summary == nil
            && discussion.links.isEmpty
            && discussion.comments.count <= 2
    }

    private func compactDiscussionDetentHeight(for discussion: ContentDiscussion) -> CGFloat {
        discussion.comments.count <= 1 ? 250 : 320
    }

    private func handleDiscussionTap(content: ContentDetail, fallbackURL: URL) {
        discussionFallbackURL = fallbackURL
        discussionUnavailableMessage = nil
        activeSheet = .discussion

        if let discussion = cachedDiscussionPayload(for: content), discussion.hasRenderableContent {
            applyDiscussionPayload(discussion)
            return
        }

        Task { await loadDiscussion(content: content, fallbackURL: fallbackURL) }
    }

    @MainActor
    private func loadDiscussion(content: ContentDetail, fallbackURL: URL, refresh: Bool = false) async {
        discussionFallbackURL = fallbackURL
        activeSheet = .discussion

        if !refresh, let discussion = cachedDiscussionPayload(for: content), discussion.hasRenderableContent {
            applyDiscussionPayload(discussion)
            return
        }

        if isLoadingDiscussion { return }

        let requestToken = UUID()
        discussionRequestToken = requestToken
        isLoadingDiscussion = true
        if refresh || cachedDiscussionPayload(for: content) == nil {
            discussionPayload = nil
        }
        discussionUnavailableMessage = nil
        defer { isLoadingDiscussion = false }

        do {
            let discussion: ContentDiscussion
            if refresh {
                discussion = try await ContentService.shared.refreshContentDiscussion(
                    id: content.id,
                    contentType: content.contentType
                )
            } else {
                discussion = try await ContentService.shared.fetchContentDiscussion(
                    id: content.id,
                    contentType: content.contentType
                )
            }

            guard discussionRequestToken == requestToken,
                  viewModel.content?.id == content.id else {
                return
            }

            applyDiscussionPayload(discussion)
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            guard discussionRequestToken == requestToken else { return }
            discussionUnavailableMessage = "Comments could not be loaded right now."
        }
    }

    @MainActor
    private func prefetchStoredDiscussion(for content: ContentDetail) async {
        guard content.contentType == .news,
              discussionURL(for: content) != nil else {
            return
        }

        let requestToken = UUID()
        discussionRequestToken = requestToken

        do {
            let discussion = try await ContentService.shared.fetchContentDiscussion(
                id: content.id,
                contentType: content.contentType
            )
            guard discussionRequestToken == requestToken,
                  viewModel.content?.id == content.id,
                  discussion.hasRenderableContent else {
                return
            }
            applyDiscussionPayload(discussion, showUnavailable: false)
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            detailLogger.debug("[ContentDetailView] Stored discussion prefetch failed | contentId=\(content.id) error=\(error.localizedDescription)")
        }
    }

    private func cachedDiscussionPayload(for content: ContentDetail) -> ContentDiscussion? {
        guard let discussionPayload,
              discussionPayload.contentId == content.id else {
            return nil
        }
        return discussionPayload
    }

    private func inlineDiscussionSummaryPayload(for content: ContentDetail) -> ContentDiscussion? {
        guard let discussion = cachedDiscussionPayload(for: content),
              discussion.summary != nil else {
            return nil
        }
        return discussion
    }

    private func discussionURL(for content: ContentDetail) -> URL? {
        let rawURL = normalizedText(content.newsDiscussionURL)
            ?? normalizedText(content.newsMetadata?.discussionURL)
        guard let rawURL else { return nil }
        return URL(string: rawURL)
    }

    private func openInAppBrowser(_ url: URL) {
        if activeSheet != nil {
            activeSheet = nil
            DispatchQueue.main.async {
                activeBrowserDestination = BrowserDestination(url: url)
            }
            return
        }
        activeBrowserDestination = BrowserDestination(url: url)
    }

    private func normalizedText(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func resetDiscussionState(fallbackURL: URL? = nil) {
        discussionRequestToken = UUID()
        discussionPayload = nil
        isLoadingDiscussion = false
        discussionFallbackURL = fallbackURL
        discussionUnavailableMessage = nil
        discussionTab = .comments
        collapsedCommentIDs = []
    }

    private func applyDiscussionPayload(
        _ discussion: ContentDiscussion,
        showUnavailable: Bool = true
    ) {
        discussionPayload = discussion
        if discussion.hasRenderableContent {
            discussionUnavailableMessage = nil
            discussionTab = .comments
            collapsedCommentIDs = []
        } else if showUnavailable {
            discussionUnavailableMessage = discussion.unavailableMessage
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
                                        .font(.appSubheadline)
                                        .foregroundColor(Color.onSurfaceSecondary)
                                } else {
                                    ForEach(discussion.discussionGroups) { group in
                                        VStack(alignment: .leading, spacing: 8) {
                                            Text(group.label)
                                                .font(.appHeadline)
                                            ForEach(group.items) { item in
                                                if let url = URL(string: item.url) {
                                                    Button {
                                                        openInAppBrowser(url)
                                                    } label: {
                                                        HStack(spacing: 8) {
                                                            Image(systemName: "arrow.up.right.square")
                                                            Text(item.title)
                                                                .multilineTextAlignment(.leading)
                                                        }
                                                    }
                                                    .buttonStyle(.plain)
                                                }
                                            }
                                        }
                                        .padding(.bottom, 4)
                                    }
                                }
                            }
                            .padding(.horizontal, Spacing.appHorizontalMargin)
                            .padding(.vertical, 16)
                        }
                    } else {
                        if discussion.summary != nil {
                            ScrollView {
                                let commentIndex = buildDiscussionCommentIndex(from: discussion.comments)
                                let linksOutsideSummary = discussion.linksOutsideSummary
                                VStack(alignment: .leading, spacing: 0) {
                                    discussionSummaryContent(discussion)
                                    if !linksOutsideSummary.isEmpty {
                                        linksTabContent(
                                            links: linksOutsideSummary,
                                            commentsByID: commentIndex.commentsByID
                                        )
                                    }
                                    if !discussion.comments.isEmpty {
                                        commentsTabContent(commentIndex: commentIndex)
                                    }
                                }
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
                                    .padding(.horizontal, Spacing.appHorizontalMargin)
                                    .padding(.vertical, 10)
                                }

                                ScrollView {
                                    let commentIndex = buildDiscussionCommentIndex(from: discussion.comments)
                                    switch discussionTab {
                                    case .comments:
                                        commentsTabContent(commentIndex: commentIndex)
                                    case .links:
                                        linksTabContent(
                                            links: discussion.links,
                                            commentsByID: commentIndex.commentsByID
                                        )
                                    }
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
                .font(.appSubheadline)
                .foregroundColor(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }

    @ViewBuilder
    private var discussionUnavailableView: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Discussion unavailable")
                    .font(.appHeadline)

                Text(discussionUnavailableText)
                    .font(.appSubheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let url = discussionResolvedFallbackURL {
                Button {
                    activeSheet = nil
                    openInAppBrowser(url)
                } label: {
                    Label("Open original discussion", systemImage: "arrow.up.right.square")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
            }

            if let content = viewModel.content, let url = discussionResolvedFallbackURL {
                Button("Try again") {
                    Task { await loadDiscussion(content: content, fallbackURL: url, refresh: true) }
                }
                .buttonStyle(.bordered)
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
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
    private func communityDiscussionSummarySection(discussion: ContentDiscussion, content: ContentDetail) -> some View {
        if let summary = discussion.summary {
            VStack(alignment: .leading, spacing: 14) {
                discussionSummaryHeader(summary: summary, discussion: discussion, content: content)

                if !summary.topics.isEmpty {
                    VStack(alignment: .leading, spacing: 16) {
                        ForEach(Array(summary.topics.prefix(4))) { topic in
                            discussionTopicRow(topic)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func discussionSummaryHeader(
        summary: DiscussionSummary,
        discussion: ContentDiscussion,
        content: ContentDetail
    ) -> some View {
        ReaderSectionHeader("Comments") {
            Spacer(minLength: 10)

            if let url = discussionSummaryURL(summary: summary, discussion: discussion) {
                HStack(alignment: .center, spacing: 4) {
                    Button {
                        handleDiscussionTap(content: content, fallbackURL: url)
                    } label: {
                        discussionHeaderIcon("bubble.left.and.bubble.right")
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Open comments")
                    .accessibilityIdentifier("content.discussion.open")

                    Button {
                        openInAppBrowser(url)
                    } label: {
                        discussionHeaderIcon("arrow.up.right.square")
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Open original discussion")
                }
                .fixedSize(horizontal: true, vertical: false)
            }
        }
    }

    private func discussionHeaderIcon(_ systemName: String) -> some View {
        Image(systemName: systemName)
            .font(.appSymbol(size: 17, weight: .regular))
            .foregroundColor(Color.onSurfaceSecondary)
            .frame(width: 40, height: 40)
            .contentShape(Rectangle())
    }

    private func discussionSummaryURL(
        summary: DiscussionSummary,
        discussion: ContentDiscussion
    ) -> URL? {
        let rawURL = summary.externalDiscussionURL ?? discussion.discussionURL ?? discussion.sourceURL
        guard let rawURL else { return nil }
        return URL(string: rawURL)
    }

    @ViewBuilder
    private func discussionTopicRow(_ topic: DiscussionSummaryTopic) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            if let stance = topic.stance {
                Text(stance)
                    .font(.appFootnote.weight(.semibold))
                    .foregroundColor(Color.onSurfaceSecondary)
                    .textCase(.uppercase)
                    .tracking(0.6)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            Text(topic.summary)
                .font(.appCallout)
                .foregroundColor(Color.readerBodyText)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func discussionRepresentativeCommentRow(_ comment: DiscussionSummaryComment) -> some View {
        HStack(alignment: .top, spacing: 10) {
            RoundedRectangle(cornerRadius: 1)
                .fill(Color.outlineVariant.opacity(0.28))
                .frame(width: 2)

            VStack(alignment: .leading, spacing: 4) {
                Text(comment.author ?? "unknown")
                    .font(.appCaption2)
                    .fontWeight(.semibold)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .textCase(.uppercase)
                    .tracking(0.4)
                Text(comment.text)
                    .font(.appFootnote)
                    .foregroundColor(Color.readerBodyText)
                    .lineSpacing(1)
                    .fixedSize(horizontal: false, vertical: true)
                if let reason = comment.reason {
                    Text(reason)
                        .font(.appCaption2)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .lineLimit(1)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func discussionSummaryContent(_ discussion: ContentDiscussion) -> some View {
        if let summary = discussion.summary {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 8) {
                    detailSectionHeaderText("Community Summary")

                    Text(summary.overview)
                        .font(.appCallout)
                        .foregroundColor(Color.readerBodyText)
                        .fixedSize(horizontal: false, vertical: true)

                    if let urlString = summary.externalDiscussionURL ?? discussion.discussionURL ?? discussion.sourceURL,
                       let url = URL(string: urlString) {
                        Button {
                            openInAppBrowser(url)
                        } label: {
                            Label("Open original discussion", systemImage: "arrow.up.right.square")
                        }
                        .buttonStyle(.plain)
                        .font(.appSubheadline)
                        .padding(.top, 4)
                    }
                }

                if !summary.topics.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        detailSectionHeaderText("Key Topics")

                        ForEach(summary.topics) { topic in
                            VStack(alignment: .leading, spacing: 5) {
                                Text(topic.title.uppercased())
                                    .font(.appCallout.weight(.bold))
                                    .foregroundColor(Color.readerBodyText)
                                    .tracking(0.4)
                                Text(topic.summary)
                                    .font(.appSubheadline)
                                    .foregroundColor(Color.readerBodyText)
                                    .fixedSize(horizontal: false, vertical: true)
                                if let stance = topic.stance {
                                    Text(stance)
                                        .font(.appCaption)
                                        .foregroundColor(Color.onSurfaceSecondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            .padding(12)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.surfaceSecondary)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                    }
                }

                if !summary.representativeComments.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        detailSectionHeaderText("Representative Comments")

                        ForEach(summary.representativeComments) { comment in
                            VStack(alignment: .leading, spacing: 5) {
                                Text(comment.author ?? "unknown")
                                    .font(.appCaption)
                                    .fontWeight(.medium)
                                    .foregroundColor(Color.onSurfaceSecondary)
                                Text(comment.text)
                                    .font(.appSubheadline)
                                    .foregroundColor(Color.readerBodyText)
                                    .fixedSize(horizontal: false, vertical: true)
                                if let reason = comment.reason {
                                    Text(reason)
                                        .font(.appCaption)
                                        .foregroundColor(Color.onSurfaceSecondary)
                                }
                            }
                            .padding(12)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.surfaceSecondary)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                    }
                }

                if !summary.notableLinks.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        detailSectionHeaderText("Notable Links")

                        ForEach(summary.notableLinks) { link in
                            if let url = URL(string: link.url) {
                                Button {
                                    openInAppBrowser(url)
                                } label: {
                                    VStack(alignment: .leading, spacing: 5) {
                                        HStack(spacing: 6) {
                                            Image(systemName: "arrow.up.right.square")
                                            Text(link.title ?? link.url)
                                                .fontWeight(.medium)
                                                .multilineTextAlignment(.leading)
                                        }
                                        .font(.appSubheadline)

                                        if let reason = link.reason {
                                            Text(reason)
                                                .font(.appCaption)
                                                .foregroundColor(Color.onSurfaceSecondary)
                                                .multilineTextAlignment(.leading)
                                        }
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                }
                                .buttonStyle(.plain)
                                .padding(12)
                                .background(Color.surfaceSecondary)
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                            }
                        }
                    }
                }
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 16)
        }
    }

    @ViewBuilder
    private func commentsTabContent(commentIndex: DiscussionCommentIndex) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if commentIndex.orderedComments.isEmpty {
                Text("No comments available.")
                    .font(.appSubheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .padding(.top, 20)
                    .frame(maxWidth: .infinity)
            } else {
                ForEach(commentIndex.orderedComments) { comment in
                    if !isHiddenByCollapse(comment, commentsByID: commentIndex.commentsByID) {
                        let indent = CGFloat(min(comment.depth, 5)) * 16
                        let isCollapsed = collapsedCommentIDs.contains(comment.commentID)
                        let childCount = commentIndex.descendantCountByID[comment.commentID] ?? 0

                        VStack(alignment: .leading, spacing: 6) {
                            if !isCollapsed {
                                Text(comment.compactText ?? comment.text)
                                    .font(.appCallout)
                                    .fontWeight(.regular)
                                    .foregroundColor(Color.readerBodyText)
                                    .fixedSize(horizontal: false, vertical: true)
                            } else if childCount > 0 {
                                HStack(spacing: 6) {
                                    Text("+\(childCount)")
                                        .font(.appCaption2)
                                        .fontWeight(.semibold)
                                        .foregroundColor(.terracottaPrimary)
                                        .padding(.horizontal, 5)
                                        .padding(.vertical, 1)
                                        .background(Color.terracottaPrimary.opacity(0.12))
                                        .clipShape(Capsule())

                                    Image(systemName: "chevron.right")
                                        .font(.appCaption2)
                                        .foregroundColor(Color.onSurfaceSecondary.opacity(0.6))
                                }
                            }
                        }
                        .padding(12)
                        .background(Color.surfaceSecondary)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .overlay(alignment: .leading) {
                            if comment.depth > 0 {
                                RoundedRectangle(cornerRadius: 1.5)
                                    .fill(Color.terracottaPrimary)
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
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 16)
    }

    @ViewBuilder
    private func linksTabContent(
        links: [DiscussionLink],
        commentsByID: [String: DiscussionComment]
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if links.isEmpty {
                Text("No links found.")
                    .font(.appSubheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .padding(.top, 20)
                    .frame(maxWidth: .infinity)
            } else {
                ForEach(links) { link in
                    if let url = URL(string: link.url) {
                        let addState = viewModel.discussionLinkAddState(for: link.id)

                        VStack(alignment: .leading, spacing: 10) {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(link.title ?? link.url)
                                    .font(.appCallout)
                                    .fontWeight(.medium)
                                    .foregroundColor(Color.onSurface)
                                    .multilineTextAlignment(.leading)
                                    .lineLimit(2)

                                Text(link.url)
                                    .font(.appCaption2)
                                    .foregroundColor(Color.onSurfaceSecondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)

                                if let commentID = link.commentID,
                                   let comment = commentsByID[commentID] {
                                    Text(comment.compactText ?? String(comment.text.prefix(120)))
                                        .font(.appCaption)
                                        .foregroundColor(Color.onSurfaceSecondary)
                                        .lineLimit(2)
                                        .padding(.top, 2)
                                }

                                HStack(spacing: 4) {
                                    Image(systemName: "arrow.up.right")
                                        .font(.appCaption2)
                                    Text(link.source)
                                        .font(.appCaption2)
                                }
                                .foregroundColor(.onSurfaceSecondary)
                            }

                            HStack(spacing: 10) {
                                Button {
                                    openInAppBrowser(url)
                                } label: {
                                    Label("Open", systemImage: "arrow.up.right.square")
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.bordered)

                                Button {
                                    Task { await viewModel.addDiscussionLinkToLongForm(link) }
                                } label: {
                                    HStack(spacing: 6) {
                                        if addState == .adding {
                                            ProgressView()
                                                .controlSize(.small)
                                        } else {
                                            Image(systemName: discussionLinkAddIcon(for: addState))
                                        }
                                        Text(discussionLinkAddTitle(for: addState))
                                    }
                                    .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.borderedProminent)
                                .tint(Color.terracottaPrimary)
                                .disabled(isLinkActionDisabled(addState))
                            }
                        }
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.surfaceSecondary)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                }
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 16)
    }

    // MARK: - Modern Section Components (Flat, no borders)
    @ViewBuilder
    private func relevantLinksSection(links: [RelevantLink]) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            ReaderSectionHeader("Links") {
                Spacer(minLength: 10)
                Text("\(links.count)")
                    .font(.appCaption.monospacedDigit().weight(.semibold))
                    .foregroundColor(Color.brandPrimary)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.brandPrimary.opacity(0.12), in: Capsule())
            }

            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(links.enumerated()), id: \.element.id) { index, link in
                    relevantLinkRow(link)
                    if index < links.count - 1 {
                        Divider()
                            .overlay(Color.outlineVariant.opacity(0.35))
                            .padding(.vertical, 12)
                    }
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Links, \(links.count)")
    }

    @ViewBuilder
    private func relevantLinkRow(_ link: RelevantLink) -> some View {
        if let url = URL(string: link.url) {
            let state = viewModel.relevantLinkReadLaterState(for: link.id)
            HStack(alignment: .top, spacing: 10) {
                Button {
                    openInAppBrowser(url)
                } label: {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(link.title ?? link.url)
                            .font(.appCallout.weight(.semibold))
                            .foregroundColor(Color.readerBodyText)
                            .multilineTextAlignment(.leading)
                            .lineLimit(3)
                            .fixedSize(horizontal: false, vertical: true)

                        Text(link.reason)
                            .font(.appFootnote)
                            .foregroundColor(Color.onSurfaceSecondary)
                            .multilineTextAlignment(.leading)
                            .lineLimit(3)
                            .fixedSize(horizontal: false, vertical: true)

                        HStack(spacing: 6) {
                            if let source = relevantLinkSourceLabel(link.source) {
                                Text(source)
                                    .font(.appCaption2)
                                    .fontWeight(.semibold)
                                    .foregroundColor(Color.onSurfaceTertiary)
                                    .textCase(.uppercase)
                                    .tracking(0.4)
                            }

                            Text(link.url)
                                .font(.appCaption2)
                                .foregroundColor(Color.onSurfaceTertiary)
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("content.relevant_link.\(link.id)")

                Spacer(minLength: 0)

                Button {
                    Task { await viewModel.addRelevantLinkToReadLater(link) }
                } label: {
                    Group {
                        if state == .adding {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Image(systemName: relevantLinkReadLaterIcon(for: state))
                        }
                    }
                    .font(.appSubheadline.weight(.medium))
                    .foregroundColor(state == .added ? .brandPrimary : Color.onSurfaceSecondary.opacity(0.78))
                    .frame(width: 40, height: 40)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(isLinkActionDisabled(state))
                .accessibilityLabel(relevantLinkReadLaterTitle(for: state))
                .accessibilityIdentifier("content.relevant_link.read_later.\(link.id)")
            }
        }
    }

    private func relevantLinkSourceLabel(_ source: String?) -> String? {
        switch source?.lowercased() {
        case "article":
            return "Article"
        case "community":
            return "Community"
        default:
            return nil
        }
    }

    private func isLinkActionDisabled(_ state: LinkReadLaterState) -> Bool {
        state == .adding || state == .added
    }

    private func discussionLinkAddTitle(for state: DiscussionLinkAddState) -> String {
        switch state {
        case .idle:
            return "Add to Long Form"
        case .adding:
            return "Adding"
        case .added:
            return "Added"
        case .failed:
            return "Retry"
        }
    }

    private func discussionLinkAddIcon(for state: DiscussionLinkAddState) -> String {
        switch state {
        case .idle:
            return "plus"
        case .adding:
            return "plus"
        case .added:
            return "checkmark"
        case .failed:
            return "arrow.clockwise"
        }
    }

    private func relevantLinkReadLaterTitle(for state: LinkReadLaterState) -> String {
        switch state {
        case .idle:
            return "Read Later"
        case .adding:
            return "Adding"
        case .added:
            return "Saved"
        case .failed:
            return "Retry"
        }
    }

    private func relevantLinkReadLaterIcon(for state: LinkReadLaterState) -> String {
        switch state {
        case .idle:
            return "bookmark"
        case .adding:
            return "bookmark"
        case .added:
            return "checkmark"
        case .failed:
            return "arrow.clockwise"
        }
    }

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
                            .font(.readerBody.weight(.bold))
                            .foregroundColor(Color.readerBodyText)
                        detailSectionHeaderText(title)
                    }

                    Spacer()

                    Image(systemName: "chevron.right")
                        .font(.appCaption2)
                        .fontWeight(.bold)
                        .foregroundColor(Color.onSurfaceSecondary.opacity(0.6))
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

    private func detailMarkdownBody(_ markdown: String, content: ContentDetail) -> some View {
        SelectableMarkdownView(
            markdown: markdown,
            textColor: .appReaderBodyText,
            baseFont: detailBodyUIFont,
            adjustsFontForContentSizeCategory: true,
            onDigDeeper: { selectedText in
                handleReaderDigDeeper(selectedText: selectedText, content: content)
            }
        )
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var detailBodyUIFont: UIFont {
        .appReaderBody
    }

    private func detailSectionHeaderText(
        _ title: String,
        color: Color = Color.readerBodyText
    ) -> some View {
        Text(title.uppercased())
            .font(.readerBody.weight(.bold))
            .foregroundColor(color)
            .tracking(0.4)
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

    private var statusIcon: String {
        guard let content = viewModel.content else { return "circle" }
        switch content.status {
        case .completed:
            return "checkmark.circle.fill"
        case .failed:
            return "xmark.circle.fill"
        case .processing:
            return "arrow.clockwise.circle.fill"
        default:
            return "circle"
        }
    }

    private var statusColor: Color {
        guard let content = viewModel.content else { return .secondary }
        switch content.status {
        case .completed:
            return .statusActive
        case .failed:
            return .statusDestructive
        case .processing:
            return .terracottaPrimary
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
            "[ContentDetailView] summary snapshot (\(context)) id=\(content.id) type=\(content.contentType.rawValue, privacy: .public) editorial_v1=\(content.editorialSummary != nil) bulleted_v1=\(content.bulletedSummary != nil) structured=\(content.structuredSummary != nil) interleaved_v1=\(content.interleavedSummary != nil) interleaved_v2=\(content.interleavedSummaryV2 != nil) editorial_key_points=\(editorialCount) bulleted_points=\(bulletedCount) structured_points=\(structuredCount) interleaved_insights=\(interleavedV1Count) interleaved_key_points=\(interleavedV2Count) raw_bullets=\(content.bulletPoints.count)"
        )
    }

    private func logSummarySection(
        content: ContentDetail,
        section: String,
        bulletPointCount: Int,
        insightCount: Int
    ) {
        detailLogger.info(
            "[ContentDetailView] summary section (\(section)) id=\(content.id) type=\(content.contentType.rawValue, privacy: .public) points=\(bulletPointCount) insights=\(insightCount)"
        )
    }

    private var navigationSurfaceName: String {
        navigationContext.surface.rawValue
    }

    private func navigateToNext() {
        guard currentIndex < allContentIds.count - 1 else {
            return
        }
        guard let fromContentId = contentId(at: currentIndex, context: "navigate_next_from"),
              let toContentId = contentId(at: currentIndex + 1, context: "navigate_next_to") else {
            return
        }
        detailLogger.info(
            "[DetailNavigation] navigateNext surface=\(navigationSurfaceName, privacy: .public) fromIndex=\(currentIndex, privacy: .public) toIndex=\(currentIndex + 1, privacy: .public) fromContentId=\(fromContentId, privacy: .public) toContentId=\(toContentId, privacy: .public)"
        )
        didTriggerNavigation = true
        navigationDirection = 1
        currentIndex += 1
    }
    
    private func navigateToPrevious() {
        guard currentIndex > 0 else {
            return
        }
        guard let fromContentId = contentId(at: currentIndex, context: "navigate_previous_from"),
              let toContentId = contentId(at: currentIndex - 1, context: "navigate_previous_to") else {
            return
        }
        detailLogger.info(
            "[DetailNavigation] navigatePrevious surface=\(navigationSurfaceName, privacy: .public) fromIndex=\(currentIndex, privacy: .public) toIndex=\(currentIndex - 1, privacy: .public) fromContentId=\(fromContentId, privacy: .public) toContentId=\(toContentId, privacy: .public)"
        )
        didTriggerNavigation = true
        navigationDirection = -1
        currentIndex -= 1
    }

}

private struct SheetOptionButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .opacity(configuration.isPressed ? 0.82 : 1)
            .animation(.spring(response: 0.24, dampingFraction: 0.86), value: configuration.isPressed)
    }
}

private struct ChatSheetButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .opacity(configuration.isPressed ? 0.88 : 1)
            .animation(.spring(response: 0.24, dampingFraction: 0.86), value: configuration.isPressed)
    }
}

private extension View {
    func detailActionBarSegment() -> some View {
        self
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
    }

    func miniSheetOptionSurface() -> some View {
        self
            .padding(.vertical, 10)
            .padding(.horizontal, 12)
            .frame(minHeight: 56)
            .background(Color.surfaceSecondary)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.28), lineWidth: 0.5)
            )
    }

    func chatTileSurface() -> some View {
        self
            .padding(12)
            .frame(maxWidth: .infinity, minHeight: 112, alignment: .topLeading)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(Color.surfaceSecondary)
                    .shadow(color: Color.black.opacity(0.16), radius: 10, x: 0, y: 5)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.34), lineWidth: 0.5)
            )
    }

    func chatWideActionSurface() -> some View {
        self
            .padding(.vertical, 12)
            .padding(.horizontal, 12)
            .frame(minHeight: 66)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(Color.surfaceSecondary)
                    .shadow(color: Color.black.opacity(0.14), radius: 9, x: 0, y: 4)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.32), lineWidth: 0.5)
            )
    }
}
