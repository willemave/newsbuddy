//
//  OnboardingFlowView.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import SwiftUI

struct OnboardingFlowView: View {
    @State private var viewModel: OnboardingViewModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private let onFinish: (OnboardingCompleteResponse) -> Void
    @Namespace private var logoNamespace

    init(
        viewModel: OnboardingViewModel,
        onFinish: @escaping (OnboardingCompleteResponse) -> Void
    ) {
        _viewModel = State(initialValue: viewModel)
        self.onFinish = onFinish
    }

    var body: some View {
        ZStack {
            Color.surfacePrimary.ignoresSafeArea()

            VStack(spacing: 0) {
                // The welcome is not a step: the rail and the docked guide arrive together
                // once a start is chosen, so progress visibly begins rather than stalling.
                if viewModel.step != .intro {
                    HStack(spacing: 12) {
                        // Loading centres the full-size Buddy, so the guide steps aside
                        // there while keeping its slot and the rail's width.
                        OnboardingGuideBuddy()
                            .matchedGeometryEffect(id: "welcomeBuddy", in: logoNamespace)
                            .frame(width: 34, height: 34)
                            .opacity(viewModel.step == .loading ? 0 : 1)

                        OnboardingProgressHeader(
                            step: viewModel.step,
                            reduceMotion: reduceMotion
                        )
                    }
                    .frame(height: 34)
                    .padding(.horizontal, Spacing.appHorizontalMargin)
                    .padding(.top, 6)
                    .transition(.opacity)
                }

                content
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            if viewModel.isLoading {
                Color.black.opacity(0.15)
                    .ignoresSafeArea()
                LoadingOverlay(message: viewModel.loadingMessage)
            }
        }
        .onChange(of: viewModel.completionResponse) { _, response in
            if let response {
                onFinish(response)
            }
        }
        .task {
            await viewModel.resumeDiscoveryIfNeeded()
        }
        .animation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.emphasized),
            value: viewModel.step
        )
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.step {
        case .intro:
            OnboardingIntroStep(viewModel: viewModel, logoNamespace: logoNamespace)
                .transition(screenTransition)
        case .choice:
            OnboardingChoiceStep(viewModel: viewModel)
                .transition(screenTransition)
        case .audio:
            OnboardingAudioStep(viewModel: viewModel)
                .transition(screenTransition)
        case .loading:
            OnboardingLoadingStep(
                viewModel: viewModel,
                reduceMotion: reduceMotion
            )
            .transition(screenTransition)
        case .suggestions:
            OnboardingSuggestionsStep(viewModel: viewModel)
                .transition(screenTransition)
        case .fastNews, .aggregators:
            OnboardingAggregatorsStep(
                viewModel: viewModel,
                reduceMotion: reduceMotion
            )
            .transition(screenTransition)
        case .reddit:
            OnboardingRedditStep(viewModel: viewModel)
                .transition(screenTransition)
        }
    }

    private var screenTransition: AnyTransition {
        .asymmetric(
            insertion: .opacity.combined(with: .move(edge: .bottom)),
            removal: .opacity.combined(with: .offset(y: -10))
        )
    }
}

/// The small upper-left guide beside the progress rail: the vector Buddy, blinking now and
/// then so he reads as present without drawing the eye from the step's content.
private struct OnboardingGuideBuddy: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion)) { timeline in
            BuddyGlyph(blink: reduceMotion ? 0 : blink(at: timeline.date.timeIntervalSinceReferenceDate))
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Newsbuddy onboarding guide")
    }

    private func blink(at time: TimeInterval) -> CGFloat {
        let phase = time.truncatingRemainder(dividingBy: 5.2)
        guard phase < 0.18 else { return 0 }
        return CGFloat(sin(phase / 0.18 * .pi))
    }
}
