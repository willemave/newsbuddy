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
    /// Captured at construction: a resumed flow skips the guide's arrival.
    private let startsAtIntro: Bool

    init(
        viewModel: OnboardingViewModel,
        onFinish: @escaping (OnboardingCompleteResponse) -> Void
    ) {
        _viewModel = State(initialValue: viewModel)
        self.onFinish = onFinish
        self.startsAtIntro = viewModel.step == .intro
    }

    var body: some View {
        ZStack {
            Color.surfacePrimary.ignoresSafeArea()

            VStack(spacing: 0) {
                OnboardingProgressHeader(
                    step: viewModel.step,
                    reduceMotion: reduceMotion
                )
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 14)
                .padding(.bottom, 4)

                content
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            if viewModel.isLoading {
                Color.black.opacity(0.15)
                    .ignoresSafeArea()
                LoadingOverlay(message: viewModel.loadingMessage)
            }

            if viewModel.step != .loading {
                OnboardingBuddyGuide(
                    reduceMotion: reduceMotion,
                    introducing: startsAtIntro
                )
                .transition(.opacity)
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
            OnboardingIntroStep(viewModel: viewModel)
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

/// The guide introduces itself at full size, blinks, then withdraws to the upper-left
/// corner and stays there as a small companion for the rest of the flow.
private struct OnboardingBuddyGuide: View {
    private enum Phase {
        case hidden
        case expanded
        case docked
    }

    let reduceMotion: Bool
    /// False when the flow resumes past the welcome, where an arrival would be a non sequitur.
    let introducing: Bool

    @State private var phase = Phase.hidden
    @State private var floating = false
    @State private var eyesClosed = false

    var body: some View {
        GeometryReader { proxy in
            Image(eyesClosed ? "BuddyMarkBlink" : "BuddyMark")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: buddySize, height: buddySize)
                .appShadow(phase == .docked ? .floating : .elevated)
                .scaleEffect(phase == .hidden ? 0.62 : 1)
                .opacity(phase == .hidden ? 0 : 1)
                .rotationEffect(
                    .degrees(phase == .docked && floating ? -3 : 0),
                    anchor: .bottom
                )
                .offset(y: phase == .docked && floating ? -2 : 0)
                .position(position(in: proxy))
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Newsbuddy onboarding guide")
        }
        .allowsHitTesting(false)
        .task { await performArrival() }
    }

    /// Arrive, blink twice, then dock. Reduced motion gets the settled end state directly.
    private func performArrival() async {
        guard phase == .hidden else { return }

        guard introducing, !reduceMotion else {
            phase = .docked
            return
        }

        withAnimation(.spring(response: 0.52, dampingFraction: 0.68)) {
            phase = .expanded
        }
        guard await pause(for: .milliseconds(620)) else { return }

        for gap in [Duration.milliseconds(200), .milliseconds(360)] {
            eyesClosed = true
            guard await pause(for: .milliseconds(110)) else { return }
            eyesClosed = false
            guard await pause(for: gap) else { return }
        }

        withAnimation(.spring(response: 0.68, dampingFraction: 0.82)) {
            phase = .docked
        }
        guard await pause(for: .milliseconds(520)) else { return }

        withAnimation(AppMotion.landingFloat) { floating = true }
    }

    /// Sleep, reporting whether the sequence should carry on.
    private func pause(for duration: Duration) async -> Bool {
        try? await Task.sleep(for: duration)
        return !Task.isCancelled
    }

    private var buddySize: CGFloat {
        phase == .docked ? 54 : 164
    }

    private func position(in proxy: GeometryProxy) -> CGPoint {
        if phase == .docked {
            return CGPoint(
                x: Spacing.appHorizontalMargin + 14,
                y: proxy.safeAreaInsets.top + 58
            )
        }

        return CGPoint(
            x: proxy.size.width / 2,
            y: max(190, proxy.safeAreaInsets.top + 150)
        )
    }
}
