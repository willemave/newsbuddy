#if DEBUG
import SwiftUI

/// Stable, backend-free rendering of real product views for visual regression evidence.
/// It is reachable only from an explicit DEBUG E2E launch argument.
struct E2EVisualStateView: View {
    let state: String
    let authViewModel: AuthenticationViewModel

    var body: some View {
        switch state {
        case "launch":
            LoadingView()
        case "landing":
            LandingView()
                .environment(authViewModel)
        case "onboarding-intro":
            OnboardingFlowView(viewModel: makeOnboardingViewModel(step: .intro)) { _ in }
        case "onboarding-audio":
            OnboardingFlowView(viewModel: makeOnboardingViewModel(step: .audio)) { _ in }
        case "onboarding-loading":
            OnboardingFlowView(viewModel: makeOnboardingViewModel(step: .loading)) { _ in }
        case "briefing-loading":
            BriefingLoadingView()
        case "briefing-header":
            VStack(spacing: 0) {
                EditorialMastheadHeader(
                    title: "Briefing",
                    titleAccessibilityIdentifier: "briefing.screen",
                    trailingAccessory: AnyView(
                        BriefingListenButton(
                            isPreparing: false,
                            isPlaying: false,
                            onToggle: {}
                        )
                    )
                )
                Spacer()
            }
            .background(Color.surfacePrimary)
        case "detail-action-bar":
            E2EDetailActionBarVisualState()
        case "briefing-player":
            E2EBriefingPlayerVisualState()
        case "knowledge-processing":
            E2EKnowledgeProcessingVisualState()
        case "briefing-tier-error":
            E2EBriefingTierErrorVisualState()
        case "briefing-tier-empty":
            NavigationStack {
                BriefingTierProgressView(progress: APIBriefingFirstRunTierProgress(tier: "audio", discovered: 0, ready: 0, processing: 0, failed: 0, skipped: 0, sourceCount: 0, pendingSources: 0, unavailableSources: 0), title: "Podcasts")
                    .padding(.horizontal, Spacing.appHorizontalMargin)
            }
        case "briefing-tier-progress":
            ScrollView {
                BriefingTierProgressView(
                    progress: APIBriefingFirstRunTierProgress(tier: "audio", sourceNames: ["Acquired", "The Knowledge Project"], discovered: 8, ready: 2, processing: 5, failed: 1, skipped: 0, sourceCount: 4, pendingSources: 1, unavailableSources: 0),
                    title: "Podcasts"
                )
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 80)
            }
            .background(Color.surfacePrimary)
        case "briefing-start-here":
            BriefingStartHereView(
                progress: Self.startHereProgress,
                scrollToTopRequest: 0,
                onRefresh: {}
            )
        default:
            Text("Unknown E2E visual state")
                .accessibilityIdentifier("e2e.visual.unknown")
        }
    }

    /// Mid-run: some sources read, one still going, one that could not be reached.
    private static let startHereProgress = APIBriefingFirstRunProgress(
        runId: 999_901,
        revision: 3,
        phase: .waiting_for_content,
        connectedSourceCount: 4,
        completedSources: [
            APIBriefingFirstRunSourceProgress(
                displayName: "Stratechery",
                processedItemCount: 6,
                outcome: .processed
            ),
            APIBriefingFirstRunSourceProgress(
                displayName: "The Verge",
                processedItemCount: 11,
                outcome: .processed
            ),
            APIBriefingFirstRunSourceProgress(
                displayName: "Hacker News",
                processedItemCount: 0,
                outcome: .unavailable
            ),
        ],
        activeSources: ["Ars Technica"],
        queuedSources: [],
        readyCategoryKeys: []
    )

    @MainActor
    private func makeOnboardingViewModel(step: OnboardingStep) -> OnboardingViewModel {
        let defaults = UserDefaults(suiteName: "newsly.e2e.visual-state") ?? .standard
        defaults.removePersistentDomain(forName: "newsly.e2e.visual-state")
        let now = Date(timeIntervalSince1970: 0)
        let user = User(
            id: 999_901,
            appleId: "e2e-visual-state",
            email: "visual-state@example.invalid",
            fullName: nil,
            twitterUsername: nil,
            hasXBookmarkSync: false,
            isAdmin: false,
            isActive: true,
            hasCompletedOnboarding: false,
            hasCompletedNewUserTutorial: true,
            createdAt: now,
            updatedAt: now
        )
        let viewModel = OnboardingViewModel(
            user: user,
            service: E2EVisualOnboardingService(),
            onboardingStateStore: OnboardingStateStore(defaults: defaults)
        )
        viewModel.step = step
        viewModel.isPersonalized = step == .audio || step == .loading
        if step == .audio {
            viewModel.audioState = .recording
            viewModel.audioDurationSeconds = 2
        }
        return viewModel
    }
}

private struct E2EKnowledgeProcessingVisualState: View {
    @State private var isProcessing = true

    private let rows = [
        ("Saved article", "photo"),
        ("Learning deck", "rectangle.on.rectangle"),
        ("Chat answer", "bubble.left.and.bubble.right"),
        ("Audio narration", "waveform"),
    ]

    var body: some View {
        VStack(spacing: 16) {
            EditorialMastheadHeader(title: "Knowledge")
            ForEach(rows, id: \.0) { title, icon in
                KnowledgeTimelineRow(
                    icon: icon,
                    isBusy: isProcessing,
                    busyAccessibilityIdentifier: "knowledge.processing.\(icon)",
                    title: title,
                    subtitle: isProcessing ? "Processing" : "Ready",
                    kicker: "TODAY"
                ) {
                    EmptyView()
                }
            }
            Button(isProcessing ? "Finish processing" : "Start processing") {
                isProcessing.toggle()
            }
            .accessibilityIdentifier("knowledge.processing.toggle")
            Spacer()
        }
        .background(Color.surfacePrimary)
    }
}

private struct E2EDetailActionBarVisualState: View {
    private static let content = try! ContentDetail(
        api: APIContentDetailResponse(
            id: 999_902,
            contentType: .podcast,
            url: "https://example.invalid/episode",
            sourceUrl: nil,
            discussionUrl: nil,
            title: "A calmer way to follow the news",
            displayTitle: "A calmer way to follow the news",
            source: "Newsbuddy Radio",
            status: .completed,
            errorMessage: nil,
            retryCount: 0,
            metadata: [:],
            createdAt: Date(timeIntervalSince1970: 0),
            updatedAt: nil,
            processedAt: nil,
            checkedOutBy: nil,
            checkedOutAt: nil,
            publicationDate: nil,
            summary: nil,
            shortSummary: nil,
            summaryKind: nil,
            summaryVersion: nil,
            structuredSummary: nil,
            longformArtifact: nil,
            feedPreview: nil,
            artifactType: nil,
            previewBullets: nil,
            reasonToRead: nil,
            fullMarkdown: nil,
            bodyKind: nil,
            bodyFormat: nil,
            newsArticleUrl: nil,
            newsDiscussionUrl: nil,
            newsKeyPoints: nil,
            newsSummary: nil,
            imageUrl: nil,
            thumbnailUrl: nil,
            detectedFeed: nil
        )
    )

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("DETAIL ACTIONS")
                .kicker()

            DetailActionBar(
                content: Self.content,
                overlaid: false,
                externalURL: URL(string: Self.content.url),
                canShowReader: true,
                isLoadingReaderBody: false,
                isConverting: false,
                supportsPodcastAudio: true,
                isPodcastAudioLoading: false,
                isPodcastAudioActive: false,
                podcastAudioAccessibilityLabel: "Play episode",
                onOpenExternal: { _ in },
                onShare: {},
                readerTransitionNamespace: nil,
                onOpenReader: {},
                onDownloadMore: {},
                onConvertLinkedArticle: {},
                onToggleKnowledgeSave: {},
                onPodcastAudio: {},
                onPodcastAudioSpeed: { _ in },
                onOpenKnowledgeActions: {}
            )
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding(.top, 120)
        .background(Color.surfacePrimary)
    }
}

/// The briefing now-playing card in both shapes: the full card shown at the
/// top of a lens and the one-line bar shown once the masthead has collapsed.
private struct E2EBriefingPlayerVisualState: View {
    private static func chapter(_ id: Int, _ title: String, minutes: Int) -> AudioEpisode {
        AudioEpisode(
            id: id,
            kind: .briefing_narration,
            status: .completed,
            title: title,
            sourceContentId: nil,
            subtitle: nil,
            artworkUrl: nil,
            durationSeconds: minutes * 60,
            audioUrl: nil,
            streamUrl: nil,
            scriptText: nil,
            errorMessage: nil,
            createdAt: Date(timeIntervalSince1970: 0),
            updatedAt: nil
        )
    }

    private static let narration = BriefingNarration(
        episodeGroupId: "e2e-visual-briefing-player",
        lensKey: "ai_society",
        scope: .lens,
        title: "AI & Society",
        status: .completed,
        playable: true,
        durationSeconds: 14 * 60,
        chapters: [
            chapter(1, "Regulators circle the frontier labs", minutes: 4),
            chapter(2, "What the new copyright rulings mean for training data", minutes: 5),
            chapter(3, "A quieter week for chip export rules", minutes: 5)
        ]
    )

    @State private var podcastPlaybackService = NarrationPlaybackService()

    private static let playing = NarrationPlaybackSnapshot(
        isPlaying: true,
        canSeek: true,
        currentTime: 72,
        duration: 5 * 60,
        playbackRate: 1.25
    )

    var body: some View {
        VStack(alignment: .leading, spacing: 28) {
            Text("PLAYER · EXPANDED")
                .kicker()
            E2EBriefingPlayerPanel(narration: Self.narration, snapshot: Self.playing, isMinimized: false)

            Text("PLAYER · MINIMIZED")
                .kicker()
            E2EBriefingPlayerPanel(narration: Self.narration, snapshot: Self.playing, isMinimized: true)

            Text("PLAYER · PREPARING")
                .kicker()
            E2EBriefingPlayerPanel(
                narration: nil,
                snapshot: NarrationPlaybackSnapshot(isPreparing: true),
                isMinimized: false
            )

            Text("PODCAST ROW")
                .kicker()
            NarrationPlaybackControlRow(
                playbackService: podcastPlaybackService,
                target: nil,
                isPreparing: false,
                onTogglePlayback: {}
            )
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding(.top, 100)
        .background(Color.surfacePrimary)
    }
}

/// Inert panel that owns the shape heights the real host normally tracks.
private struct E2EBriefingPlayerPanel: View {
    let narration: BriefingNarration?
    let snapshot: NarrationPlaybackSnapshot
    let isMinimized: Bool

    @State private var fullHeight: CGFloat = 0
    @State private var minimizedHeight: CGFloat = 0

    var body: some View {
        BriefingNowPlayingPanel(
            lensTitle: "AI & Society",
            narration: narration,
            selectedIndex: 1,
            snapshot: snapshot,
            isMinimized: isMinimized,
            onTogglePlayback: {},
            onPrevious: {},
            onNext: {},
            onShowChapters: {},
            onSeek: { _ in },
            onSetPlaybackRate: { _ in },
            onExpand: {},
            onDismiss: {},
            fullHeight: $fullHeight,
            minimizedHeight: $minimizedHeight
        )
    }
}

@MainActor
private final class E2EVisualOnboardingService: OnboardingServicing {
    private let runId = 999_901
    private let topics = ["AI research", "Climate tech", "Formula 1"]
    private var polls = 0

    func audioDiscover(
        request: OnboardingAudioDiscoverRequest
    ) async throws -> OnboardingAudioDiscoverResponse {
        _ = request
        polls = 0
        return OnboardingAudioDiscoverResponse(
            runId: runId,
            runStatus: "running",
            topicSummary: "AI research, climate tech and Formula 1",
            inferredTopics: topics,
            lanes: lanes(at: 0)
        )
    }

    /// Walks the lanes from queued to done over a few seconds of polling, so the loading
    /// step shows real progress before the picks arrive.
    func discoveryStatus(runId: Int) async throws -> OnboardingDiscoveryStatusResponse {
        polls += 1
        let done = polls >= 12
        return OnboardingDiscoveryStatusResponse(
            runId: runId,
            runStatus: done ? "completed" : "running",
            topicSummary: "AI research, climate tech and Formula 1",
            inferredTopics: topics,
            lanes: lanes(at: polls),
            suggestions: done ? Self.suggestions : nil,
            errorMessage: nil
        )
    }

    private func lanes(at poll: Int) -> [OnboardingDiscoveryLaneStatus] {
        [("Newsletters", 2), ("Podcasts", 5), ("Reddit", 8)].map { name, finishesAt in
            let queries = 3
            let completed = min(queries, max(0, poll - finishesAt + queries))
            let status = poll >= finishesAt ? "completed" : (poll >= finishesAt - queries ? "processing" : "queued")
            return OnboardingDiscoveryLaneStatus(
                name: name, status: status, completedQueries: completed, queryCount: queries
            )
        }
    }

    private static let suggestions = OnboardingFastDiscoverResponse(
        recommendedPods: [
            suggestion(1, "podcast_rss", "Latent Space", "https://www.latent.space", "The AI engineer podcast"),
            suggestion(2, "podcast_rss", "Catalyst with Shayle Kann", "https://www.canarymedia.com", "Climate tech, deeply reported"),
        ],
        recommendedSubstacks: [
            suggestion(3, "substack", "Import AI", "https://importai.substack.com", "Weekly AI research digest"),
            suggestion(4, "substack", "Heatmap Daily", "https://heatmap.news", "Climate and the economy"),
            suggestion(5, "substack", "The Race", "https://the-race.com", "Formula 1 analysis"),
        ],
        recommendedSubreddits: [
            suggestion(6, "reddit", "MachineLearning", "https://www.reddit.com/r/MachineLearning", nil),
            suggestion(7, "reddit", "formula1", "https://www.reddit.com/r/formula1", nil),
        ]
    )

    private static func suggestion(
        _ id: Int, _ type: String, _ title: String, _ url: String, _ rationale: String?
    ) -> OnboardingSuggestion {
        OnboardingSuggestion(
            id: id,
            suggestionType: type,
            title: title,
            siteURL: url,
            feedURL: url + "/feed",
            subreddit: type == "reddit" ? title : nil,
            rationale: rationale,
            score: 0.9,
            isDefault: false
        )
    }

    func complete(request: OnboardingCompleteRequest) async throws -> OnboardingCompleteResponse {
        OnboardingCompleteResponse(
            status: "completed",
            taskId: nil,
            inboxCountEstimate: 0,
            configuredSourceCount: request.selectedSuggestionIds.count,
            longformStatus: "completed",
            hasCompletedOnboarding: true,
            hasCompletedNewUserTutorial: true
        )
    }
}
private struct E2EBriefingTierErrorVisualState: View {
    @State private var failed = true
    @State private var chromeCollapse = BriefingChromeCollapseModel()

    var body: some View {
        BriefingLensPageView(
            lensKey: "podcasts", lensTitle: "Podcasts", renderModel: nil,
            firstRunProgress: APIBriefingFirstRunTierProgress(tier: "audio", sourceNames: ["Acquired"], discovered: 1, ready: 0, processing: 1, failed: 0, skipped: 0, sourceCount: 1, pendingSources: 0, unavailableSources: 0),
            isReadTrackingEnabled: false, readBoundaryY: nil, documentGeneration: 0,
            scrollToTopRequest: 0, shouldScrollToTop: false,
            error: failed ? "Could not load Podcasts. Please try again." : nil,
            continuationError: nil, isLoadingContinuation: false,
            chromeCollapse: chromeCollapse, collapsibleChromeHeight: 0, topContentInset: 40,
            onOpenSource: { _ in }, onOpenDiscussion: { _ in }, onDig: { _, _ in },
            onRefresh: { failed = false }, onLoad: {}, onRetry: { failed = false },
            onFirstPassageVisible: {}, onScrolledDown: {}, onMarkSegmentSeen: { _ in }, onSetHeaderPinned: { _ in }
        )
        .background(Color.surfacePrimary)
    }
}

#endif
