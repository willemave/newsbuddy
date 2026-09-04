//
//  OnboardingSuggestionsStep.swift
//  newsly
//

import SwiftUI

struct OnboardingSuggestionsStep: View {
    let viewModel: OnboardingViewModel

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    onboardingHeaderBlock(
                        eyebrow: viewModel.isShowingDefaultConfirmation ? "QUICK START" : nil,
                        title: viewModel.isShowingDefaultConfirmation ? "Start without personalized sources" : "Your picks",
                        isLeading: true,
                        titleAccessibilityIdentifier: "onboarding.suggestions.screen"
                    )

                    if viewModel.substackSuggestions.isEmpty
                        && viewModel.podcastSuggestions.isEmpty
                    {
                        Text(emptyStateMessage)
                            .font(.appCallout)
                            .foregroundColor(.onSurfaceSecondary)
                            .padding(.vertical, 20)
                    }

                    if !viewModel.substackSuggestions.isEmpty {
                        OnboardingSuggestionSection(
                            title: "NEWSLETTERS",
                            items: viewModel.substackSuggestions,
                            isSelected: { viewModel.isSuggestionSelected($0) },
                            onToggle: { viewModel.toggleSource($0) }
                        )
                    }

                    if !viewModel.podcastSuggestions.isEmpty {
                        OnboardingSuggestionSection(
                            title: "PODCASTS",
                            items: viewModel.podcastSuggestions,
                            isSelected: { viewModel.isSuggestionSelected($0) },
                            onToggle: { viewModel.toggleSource($0) }
                        )
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 16)
                .padding(.bottom, 128)
            }

            footer
        }
    }

    private var footer: some View {
        VStack(spacing: 10) {
            onboardingPrimaryButton("Continue") {
                withAnimation(AppMotion.panel) {
                    viewModel.advanceToAggregators()
                }
            }
            .disabled(viewModel.isLoading)
            .accessibilityIdentifier("onboarding.suggestions.continue")

            if viewModel.shouldOfferRetryFromSuggestions {
                Button("Try again") {
                    withAnimation(AppMotion.panel) {
                        viewModel.retryPersonalization()
                    }
                }
                .font(.appCallout.weight(.medium))
                .foregroundColor(.onSurfaceSecondary)
                .buttonStyle(OnboardingTextButtonStyle())
                .accessibilityIdentifier("onboarding.suggestions.retry")
            } else if viewModel.isShowingDefaultConfirmation {
                Button("Personalize instead") {
                    withAnimation(AppMotion.panel) {
                        viewModel.retryPersonalization()
                    }
                }
                .font(.appCallout.weight(.medium))
                .foregroundColor(.onSurfaceSecondary)
                .buttonStyle(OnboardingTextButtonStyle())
                .accessibilityIdentifier("onboarding.suggestions.personalize")
            }

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.appCaption)
                    .foregroundColor(.statusDestructive)
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 14)
        .padding(.bottom, 16)
        .background(onboardingFooterBackground)
    }

    private var emptyStateMessage: String {
        if viewModel.isShowingDefaultConfirmation {
            return "Nothing added automatically."
        }
        return "No matches yet."
    }
}
