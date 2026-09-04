#if DEBUG
import SwiftUI

/// Stable, backend-free rendering of real product views for visual regression evidence.
/// It is reachable only from an explicit DEBUG E2E launch argument.
struct E2EVisualStateView: View {
    let state: String
    let authViewModel: AuthenticationViewModel

    var body: some View {
        switch state {
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

@MainActor
private final class E2EVisualOnboardingService: OnboardingServicing {
    private let runId = 999_901

    func audioDiscover(
        request: OnboardingAudioDiscoverRequest
    ) async throws -> OnboardingAudioDiscoverResponse {
        _ = request
        return OnboardingAudioDiscoverResponse(
            runId: runId,
            runStatus: "running",
            topicSummary: "Previewing your personalized sources",
            inferredTopics: [],
            lanes: []
        )
    }

    func discoveryStatus(runId: Int) async throws -> OnboardingDiscoveryStatusResponse {
        OnboardingDiscoveryStatusResponse(
            runId: runId,
            runStatus: "completed",
            topicSummary: "Previewing your personalized sources",
            inferredTopics: [],
            lanes: [],
            suggestions: OnboardingFastDiscoverResponse(
                recommendedPods: [],
                recommendedSubstacks: [],
                recommendedSubreddits: []
            ),
            errorMessage: nil
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
#endif
