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
