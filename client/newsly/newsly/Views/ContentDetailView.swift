//
//  ContentDetailView.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import SwiftUI
import os.log

private let detailLogger = Logger(subsystem: "com.newsly", category: "ContentDetailView")

struct ContentDetailView: View {
    @Namespace private var readerTransitionNamespace
    private let navigationContext: ContentDetailNavigationContext
    private var initialContentId: Int { navigationContext.initialContentId }
    private var initialContentType: APIContentType? { navigationContext.initialContentType }
    private var allContentIds: [Int] { navigationContext.contentIds }
    @State private var viewModel: ContentDetailViewModel
    @Environment(ReadingStateStore.self) private var readingStateStore
    @Environment(\.dismiss) private var dismiss
    @State private var chatCoordinator: DetailChatCoordinator
    @State private var currentIndex: Int
    // Navigation skipping state
    @State private var didTriggerNavigation: Bool = false
    @State private var navigationDirection: Int = 0 // +1 next, -1 previous
    // Convert button state
    @State private var isConverting: Bool = false
    // Modal presentation state
    @State private var activeSheet: DetailSheetDestination?
    @State private var pendingSheetDestination: DetailSheetDestination?
    @State private var pendingShareOption: ShareContentOption?
    @State private var podcastAudioController: PodcastAudioController
    @State private var activeAlert: ViewAlert?
    @State private var activeReaderContent: ContentDetail?
    @State private var activeBrowserDestination: BrowserDestination?
    @State private var activeLearningDeckReader: LearningDeckReaderDestination?
    @AppStorage("hasSeenLearningDeckHint") private var hasSeenLearningDeckHint = false
    @State private var showLearningDeckHint = false
    @State private var learningDeckHintBounce = false
    @State private var discussionCoordinator: DiscussionSheetCoordinator
    @State private var pendingScrollTarget: ContentDetailScrollTarget?
    @State private var detailScrollOffsetY: CGFloat = 0
    // Transcript/Full Article collapsed state
    @State private var isTranscriptExpanded: Bool = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    init(
        contentId: Int,
        contentType: APIContentType? = nil,
        allContentIds: [Int] = [],
        navigationSurface: ContentDetailNavigationSurface = .direct,
        initialScrollTarget: ContentDetailScrollTarget? = nil,
        readStateCache: ReadStateCache? = nil
    ) {
        let readStateCache = readStateCache ?? ReadStateCache()
        let context = ContentDetailNavigationContext(
            initialContentId: contentId,
            initialContentType: contentType,
            contentIds: allContentIds,
            surface: navigationSurface,
            initialScrollTarget: initialScrollTarget
        )
        self.navigationContext = context
        self._currentIndex = State(initialValue: context.initialIndex)
        self._viewModel = State(
            initialValue: RootDependencyFactory.makeContentDetailViewModel(
                contentId: contentId,
                contentType: contentType,
                readStateCache: readStateCache
            )
        )
        self._chatCoordinator = State(initialValue: RootDependencyFactory.makeDetailChatCoordinator())
        self._podcastAudioController = State(initialValue: RootDependencyFactory.makePodcastAudioController())
        self._discussionCoordinator = State(initialValue: RootDependencyFactory.makeDiscussionSheetCoordinator())
        self._pendingScrollTarget = State(initialValue: context.initialScrollTarget)
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
            ScrollViewReader { scrollProxy in
                ScrollView {
                    VStack(spacing: 0) {
                        if viewModel.isLoading {
                            ContentDetailSkeletonView()
                                .frame(minHeight: 400)
                        } else if let error = viewModel.errorMessage {
                            ErrorView(message: error) {
                                Task { await viewModel.loadContent() }
                            }
                            .frame(minHeight: 400)
                        } else if let content = viewModel.content {
                            VStack(alignment: .leading, spacing: 0) {
                                // Parallax hero header (image + title + action bar)
                                heroHeader(content: content, scrollProxy: scrollProxy)

                                Divider()
                                    .padding(.horizontal, DetailDesign.horizontalPadding)

                                // Chat status banner (inline, under header)
                                if let activeSession = chatCoordinator.activeSession(for: content) {
                                    ChatStatusBanner(
                                        session: activeSession,
                                        onTap: {
                                            chatCoordinator.openChatSession(
                                                sessionId: activeSession.id,
                                                content: content
                                            )
                                        },
                                        onDismiss: {
                                            chatCoordinator.markSessionViewed(sessionId: activeSession.id)
                                        },
                                        style: .inline
                                    )
                                    .padding(.horizontal, DetailDesign.horizontalPadding)
                                    .padding(.top, 12)
                                }

                                DetailContentSections(
                                    content: content,
                                    contentBodyText: viewModel.contentBody?.text,
                                    inlineDiscussion: discussionCoordinator.inlineSummaryPayload(for: content),
                                    isSubscribingToFeed: viewModel.isSubscribingToFeed,
                                    feedSubscriptionSuccess: viewModel.feedSubscriptionSuccess,
                                    feedSubscriptionError: viewModel.feedSubscriptionError,
                                    isTranscriptExpanded: $isTranscriptExpanded,
                                    startTopicSession: { topic in
                                        try await chatCoordinator.startTopicSession(
                                            content: content,
                                            topic: topic
                                        )
                                    },
                                    onSummaryAppear: { section, bulletPointCount, insightCount in
                                        logSummarySection(
                                            content: content,
                                            section: section,
                                            bulletPointCount: bulletPointCount,
                                            insightCount: insightCount
                                        )
                                    },
                                    onSubscribeToDetectedFeed: {
                                        Task { await viewModel.subscribeToDetectedFeed() }
                                    },
                                    onOpenURL: openInAppBrowser,
                                    linkStateForLink: { viewModel.relevantLinkReadLaterState(for: $0) },
                                    onAddRelevantLink: { link in
                                        Task { await viewModel.addRelevantLinkToReadLater(link) }
                                    },
                                    onOpenFullDiscussion: { discussionURL in
                                        presentDiscussionSheet(content: content, fallbackURL: discussionURL)
                                    },
                                    onDigDeeper: { selectedText in
                                        chatCoordinator.handleReaderDigDeeper(
                                            selectedText: selectedText,
                                            content: content,
                                            visibleContentIds: allContentIds
                                        )
                                    }
                                )

                                // Bottom spacing
                                Spacer()
                                    .frame(height: 40)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        } else {
                            ContentDetailSkeletonView()
                                .frame(minHeight: 400)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .coordinateSpace(name: "detailScroll")
                .onScrollGeometryChange(for: CGFloat.self) { geometry in
                    max(geometry.contentOffset.y, 0)
                } action: { _, offsetY in
                    detailScrollOffsetY = offsetY
                }
                .scrollClipDisabled()
                .textSelection(.enabled)
                .accessibilityIdentifier("content.detail.screen")
                .task(id: viewModel.content?.id) {
                    await Task.yield()
                    await requestInitialCommentsScrollIfNeeded()
                }
                .onChange(of: discussionCoordinator.commentsNavigationState) { _, _ in
                    resolvePendingCommentsNavigation(scrollProxy: scrollProxy)
                }
                .overlay(alignment: .topLeading) {
                    GeometryReader { proxy in
                        VStack(alignment: .leading, spacing: 0) {
                            floatingBackButton
                                .opacity(floatingBackOpacity)
                                .scaleEffect(floatingBackScale)
                                .padding(.leading, 16)
                                .padding(.top, floatingBackTopPadding(for: proxy))
                            Spacer(minLength: 0)
                        }
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
        .onChange(of: currentIndex) { _, _ in
            pendingScrollTarget = nil
        }
        .onChange(of: viewModel.content?.id) { oldValue, newValue in
            if let oldValue, oldValue != newValue {
                podcastAudioController.stopIfSpeaking(forContentId: oldValue)
            }
            guard let id = newValue, let content = viewModel.content else {
                discussionCoordinator.reset()
                return
            }
            discussionCoordinator.reset(fallbackURL: discussionCoordinator.discussionURL(for: content))
            readingStateStore.setCurrent(contentId: id, type: content.contentType)
            logSummarySnapshot(content: content, context: "content_change")
            if pendingScrollTarget != .comments && content.contentType == .news {
                Task {
                    await discussionCoordinator.prefetchStoredDiscussion(
                        for: content,
                        currentContentId: viewModel.content?.id
                    )
                }
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
            if let content = viewModel.content {
                podcastAudioController.stopIfSpeaking(for: content)
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
            chatCoordinator.chatError = nil
            discussionCoordinator.unavailableMessage = nil
            guard !presentPendingSheetIfNeeded() else { return }
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
                    .presentationDetents(
                        DiscussionSheet.presentationDetents(
                            isLoading: discussionCoordinator.isLoading,
                            discussion: discussionCoordinator.payload
                        )
                    )
                    .presentationDragIndicator(.visible)

            case .chat:
                if let content = viewModel.content {
                    chatSheet(content: content)
                        .presentationDetents([.medium, .large])
                        .presentationDragIndicator(.hidden)
                        .presentationCornerRadius(24)
                }

            case .learningDeckCreate:
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
                    chatCoordinator.handleReaderDigDeeper(
                        selectedText: selectedText,
                        content: content,
                        visibleContentIds: allContentIds
                    )
                }
            )
            .task(id: content.id) {
                await viewModel.loadReaderBody(for: content)
            }
            .contentZoomNavigationTransition(id: content.id, namespace: readerTransitionNamespace)
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

    @ViewBuilder
    private func audioPromptCard(for content: ContentDetail) -> some View {
        PodcastAudioPromptCard(
            isLoading: podcastAudioController.isLoading(for: content),
            isActive: podcastAudioController.isActive(for: content),
            statusText: podcastAudioController.statusText(for: content),
            accessibilityLabel: podcastAudioController.accessibilityLabel(for: content),
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
        )
    }

    private func podcastPlaybackControls(for content: ContentDetail) -> some View {
        NarrationPlaybackControlRow(
            playbackService: podcastAudioController.playbackService,
            target: podcastAudioController.target(for: content),
            isPreparing: podcastAudioController.isLoading(for: content),
            onTogglePlayback: {
                Task { await handlePodcastAudio(for: content) }
            }
        )
        .accessibilityIdentifier("content.audio.podcast.controls")
    }

    @MainActor
    private func handlePodcastAudio(
        for content: ContentDetail,
        rate: Float? = nil
    ) async {
        do {
            try await podcastAudioController.handleAudio(
                for: content,
                currentContentId: viewModel.content?.id,
                rate: rate
            )
        } catch {
            activeAlert = ViewAlert(
                title: "Audio",
                message: "Failed to load podcast audio: \(error.localizedDescription)"
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
              ServerImageURL.resolve(imageUrlString) != nil else {
            return false
        }
        return true
    }

    @ViewBuilder
    private func heroHeader(content: ContentDetail, scrollProxy: ScrollViewProxy) -> some View {
        DetailHeroHeader(
            content: content,
            reduceMotion: reduceMotion,
            showsPodcastPlaybackControls: podcastAudioController.shouldShowControls(for: content)
        ) { overlaid in
            actionBar(content: content, overlaid: overlaid, scrollProxy: scrollProxy)
        } podcastPlaybackControls: {
            podcastPlaybackControls(for: content)
        }
    }

    private var floatingBackButton: some View {
        FloatingBackButton(style: .imageOverlay) {
            detailLogger.info(
                "[DetailNavigation] backButtonTapped surface=\(navigationSurfaceName, privacy: .public) contentId=\(contentIdLogValue(at: currentIndex), privacy: .public) index=\(currentIndex, privacy: .public)"
            )
            dismiss()
        }
    }

    private var floatingBackOpacity: Double {
        let progress = floatingBackReductionProgress
        let opacity = 1 - progress * (1 - DetailDesign.floatingBackMinimumOpacity)
        return Double(opacity)
    }

    private var floatingBackScale: CGFloat {
        let progress = floatingBackReductionProgress
        return 1 - progress * (1 - DetailDesign.floatingBackMinimumScale)
    }

    private var floatingBackReductionProgress: CGFloat {
        guard hasHeroImage else { return 0 }
        let distance = DetailDesign.floatingBackFadeEndOffset - DetailDesign.floatingBackFadeStartOffset
        guard distance > 0 else { return 1 }
        let raw = (detailScrollOffsetY - DetailDesign.floatingBackFadeStartOffset) / distance
        return min(max(raw, 0), 1)
    }

    private func floatingBackTopPadding(for proxy: GeometryProxy) -> CGFloat {
        if hasHeroImage {
            let fallbackTopInset: CGFloat = 56
            return max(proxy.safeAreaInsets.top, fallbackTopInset) + 8
        }

        return DetailDesign.textOnlyBackButtonTopPadding
    }

    // MARK: - Modern Action Bar (Minimal, Twitter-inspired)
    @ViewBuilder
    private func actionBar(
        content: ContentDetail,
        overlaid: Bool = false,
        scrollProxy: ScrollViewProxy
    ) -> some View {
        DetailActionBar(
            content: content,
            overlaid: overlaid,
            externalURL: URL(string: content.url),
            canShowReader: viewModel.canShowReader(for: content),
            isLoadingReaderBody: viewModel.isLoadingReaderBody,
            isConverting: isConverting,
            supportsPodcastAudio: podcastAudioController.supportsAudio(for: content),
            isPodcastAudioLoading: podcastAudioController.isLoading(for: content),
            isPodcastAudioActive: podcastAudioController.isActive(for: content),
            podcastAudioAccessibilityLabel: podcastAudioController.accessibilityLabel(for: content),
            isStartingChat: chatCoordinator.isStartingChat,
            isCheckingChatSession: chatCoordinator.isCheckingChatSession,
            showLearningDeckHint: $showLearningDeckHint,
            hasSeenLearningDeckHint: $hasSeenLearningDeckHint,
            learningDeckHintBounce: $learningDeckHintBounce,
            onOpenExternal: openInAppBrowser,
            onShare: { activeSheet = .share },
            readerTransitionNamespace: readerTransitionNamespace,
            onOpenReader: { activeReaderContent = content },
            onDownloadMore: { activeSheet = .download },
            onConvertLinkedArticle: {
                Task {
                    isConverting = true
                    await viewModel.saveLinkedArticleAsKnowledge()
                    isConverting = false
                }
            },
            onToggleKnowledgeSave: {
                Task { await viewModel.toggleKnowledgeSave() }
            },
            onPodcastAudio: {
                Task { await handlePodcastAudio(for: content) }
            },
            onPodcastAudioSpeed: { option in
                Task {
                    await handlePodcastAudio(
                        for: content,
                        rate: option.rate
                    )
                }
            },
            onCreateLearningDeck: { activeSheet = .learningDeckCreate },
            onDeepDive: {
                Task {
                    if let activeSession = chatCoordinator.activeSession(for: content) {
                        chatCoordinator.openChatSession(
                            sessionId: activeSession.id,
                            content: content
                        )
                        return
                    }
                    if chatCoordinator.prepareChatSheetPresentation() {
                        activeSheet = .chat
                    }
                }
            }
        )
    }

    // MARK: - Share Sheet
    @ViewBuilder
    private var shareSheet: some View {
        DetailShareSheet(
            onClose: { activeSheet = nil },
            onQueueShare: queueShareContent,
            onOpenTweetSuggestions: {
                pendingSheetDestination = .tweet
                activeSheet = nil
            }
        )
    }

    private func queueShareContent(_ option: ShareContentOption) {
        pendingShareOption = option
        activeSheet = nil
    }

    private func presentPendingSheetIfNeeded() -> Bool {
        guard let destination = pendingSheetDestination else { return false }
        pendingSheetDestination = nil
        activeSheet = destination
        return true
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
        DetailDownloadSheet(
            onClose: { activeSheet = nil },
            onDownload: { count in
                activeSheet = nil
                Task { await viewModel.downloadMoreFromSeries(count: count) }
            }
        )
    }

    // MARK: - AI Chat Sheet
    @ViewBuilder
    private func chatSheet(content: ContentDetail) -> some View {
        DetailChatSheet(
            chatError: chatCoordinator.chatError,
            isStartingChat: chatCoordinator.isStartingChat,
            showsPodcastAudioCard: podcastAudioController.supportsAudio(for: content),
            onClose: { activeSheet = nil },
            onStartChat: {
                Task {
                    await chatCoordinator.startChat(
                        content: content,
                        visibleContentIds: allContentIds,
                        provider: .openai,
                        onCloseSheet: { activeSheet = nil }
                    )
                }
            },
            onDigDeeper: {
                Task {
                    await chatCoordinator.startDeepDive(
                        content: content,
                        visibleContentIds: allContentIds,
                        onCloseSheet: { activeSheet = nil }
                    )
                }
            },
            onCouncilChat: {
                Task {
                    await chatCoordinator.startCouncil(
                        content: content,
                        visibleContentIds: allContentIds,
                        provider: .openai,
                        onCloseSheet: { activeSheet = nil }
                    )
                }
            },
            onDeepResearch: {
                Task {
                    await chatCoordinator.startDeepResearch(
                        content: content,
                        onCloseSheet: { activeSheet = nil }
                    )
                }
            }
        ) {
            audioPromptCard(for: content)
        }
    }

    @MainActor
    private func requestInitialCommentsScrollIfNeeded() async {
        guard pendingScrollTarget == .comments,
              let content = viewModel.content else {
            return
        }
        pendingScrollTarget = nil
        guard let fallbackURL = discussionCoordinator.discussionURL(for: content) else {
            return
        }
        await requestCommentsScroll(content: content, fallbackURL: fallbackURL)
    }

    @MainActor
    private func requestCommentsScroll(
        content: ContentDetail,
        fallbackURL: URL
    ) async {
        pendingScrollTarget = .comments
        discussionCoordinator.requestCommentsNavigation(
            content: content,
            fallbackURL: fallbackURL
        )
        await discussionCoordinator.loadPendingCommentsNavigation(
            content: content,
            currentContentId: viewModel.content?.id
        )
    }

    @MainActor
    private func resolvePendingCommentsNavigation(scrollProxy: ScrollViewProxy) {
        switch discussionCoordinator.commentsNavigationAction(for: viewModel.content) {
        case .none, .waitForPayload:
            return
        case .scrollInlineSummary:
            pendingScrollTarget = nil
            withAnimation(AppMotion.subtle) {
                scrollProxy.scrollTo(ContentDetailScrollTarget.comments, anchor: .top)
            }
        case .presentSheet:
            pendingScrollTarget = nil
            activeSheet = .discussion
        }
    }

    private func presentDiscussionSheet(content: ContentDetail, fallbackURL: URL) {
        discussionCoordinator.prepareForPresentation(fallbackURL: fallbackURL)
        activeSheet = .discussion
        Task {
            await discussionCoordinator.load(
                content: content,
                fallbackURL: fallbackURL,
                currentContentId: viewModel.content?.id
            )
        }
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

    @ViewBuilder
    private var discussionSheet: some View {
        DiscussionSheet(
            isLoading: discussionCoordinator.isLoading,
            discussion: discussionCoordinator.payload,
            unavailableText: discussionCoordinator.unavailableText,
            fallbackURL: discussionCoordinator.resolvedFallbackURL,
            canRetry: viewModel.content != nil && discussionCoordinator.resolvedFallbackURL != nil,
            selectedTab: Binding(
                get: { discussionCoordinator.selectedTab },
                set: { discussionCoordinator.selectedTab = $0 }
            ),
            collapsedCommentIDs: Binding(
                get: { discussionCoordinator.collapsedCommentIDs },
                set: { discussionCoordinator.collapsedCommentIDs = $0 }
            ),
            addStateForLink: { viewModel.discussionLinkAddState(for: $0) },
            onClose: { activeSheet = nil },
            onOpenURL: openInAppBrowser,
            onRetry: {
                guard let content = viewModel.content,
                      let url = discussionCoordinator.resolvedFallbackURL else {
                    return
                }
                Task {
                    await discussionCoordinator.load(
                        content: content,
                        fallbackURL: url,
                        refresh: true,
                        currentContentId: viewModel.content?.id
                    )
                }
            },
            onAddLink: { link in
                Task { await viewModel.addDiscussionLinkToLongForm(link) }
            }
        )
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
