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
                        subtitle: subtitle,
                        isLeading: true,
                        titleAccessibilityIdentifier: "onboarding.suggestions.screen"
                    )

                    if !hasPicks, let emptyStateMessage {
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

    private var hasPicks: Bool {
        !viewModel.substackSuggestions.isEmpty || !viewModel.podcastSuggestions.isEmpty
    }

    private var subtitle: String? {
        if viewModel.isShowingDefaultConfirmation {
            return "No newsletters or podcasts yet. Next you'll choose quick news feeds to follow."
        }
        return hasPicks ? "All selected. Tap any you'd rather leave out." : nil
    }

    private var emptyStateMessage: String? {
        if viewModel.isShowingDefaultConfirmation {
            return nil
        }
        return "No newsletters or podcasts matched yet. Try again, or continue and add some later."
    }
}
