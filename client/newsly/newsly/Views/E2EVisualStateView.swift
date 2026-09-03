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
            BuddyLoadingView(message: "Preparing your briefing")
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
        return viewModel
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
