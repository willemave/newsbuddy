#if DEBUG
import SwiftUI

/// Stable, backend-free rendering of real product views for visual regression evidence.
/// It is reachable only from an explicit DEBUG E2E launch argument.
struct E2EVisualStateView: View {
    let state: String

    var body: some View {
        switch state {
        case "onboarding-intro":
            OnboardingFlowView(viewModel: makeOnboardingViewModel(step: .intro)) { _ in }
        case "onboarding-loading":
            OnboardingFlowView(viewModel: makeOnboardingViewModel(step: .loading)) { _ in }
        case "briefing-loading":
            BuddyLoadingView(message: "Preparing your briefing")
        default:
            Text("Unknown E2E visual state")
                .accessibilityIdentifier("e2e.visual.unknown")
        }
    }

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
        viewModel.isPersonalized = step == .loading
        return viewModel
    }
}

@MainActor
private final class E2EVisualOnboardingService: OnboardingServicing {
    func audioDiscover(
        request: OnboardingAudioDiscoverRequest
    ) async throws -> OnboardingAudioDiscoverResponse {
        throw CancellationError()
    }

    func discoveryStatus(runId: Int) async throws -> OnboardingDiscoveryStatusResponse {
        throw CancellationError()
    }

    func complete(request: OnboardingCompleteRequest) async throws -> OnboardingCompleteResponse {
        throw CancellationError()
    }
}
#endif
