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
                OnboardingBuddyGuide(reduceMotion: reduceMotion)
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

private struct OnboardingBuddyGuide: View {
    private enum Phase {
        case hidden
        case expanded
        case docked
    }

    let reduceMotion: Bool

    @State private var phase = Phase.hidden
    @State private var floating = false

    var body: some View {
        GeometryReader { proxy in
            Image("BuddyMark")
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
        .task {
            guard phase == .hidden else { return }

            if reduceMotion {
                phase = .docked
                return
            }

            withAnimation(.spring(response: 0.52, dampingFraction: 0.68)) {
                phase = .expanded
            }
            try? await Task.sleep(for: .milliseconds(700))
            guard !Task.isCancelled else { return }

            withAnimation(.spring(response: 0.68, dampingFraction: 0.82)) {
                phase = .docked
            }
            try? await Task.sleep(for: .milliseconds(520))
            guard !Task.isCancelled else { return }

            withAnimation(AppMotion.landingFloat) {
                floating = true
            }
        }
    }

    private var buddySize: CGFloat {
        phase == .docked ? 54 : 164
    }

    private func position(in proxy: GeometryProxy) -> CGPoint {
        if phase == .docked {
            return CGPoint(
                x: proxy.size.width - Spacing.appHorizontalMargin - 14,
                y: proxy.safeAreaInsets.top + 58
            )
        }

        return CGPoint(
            x: proxy.size.width / 2,
            y: max(210, proxy.size.height * 0.34)
        )
    }
}
