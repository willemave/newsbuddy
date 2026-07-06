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
                        eyebrow: viewModel.isShowingDefaultConfirmation ? "QUICK START" : "FINAL PICKS",
                        title: viewModel.isShowingDefaultConfirmation ? "Start without personalized sources" : "Your picks",
                        subtitle: suggestionsSubtitle,
                        isLeading: true
                    )

                    if viewModel.substackSuggestions.isEmpty
                        && viewModel.podcastSuggestions.isEmpty
                    {
                        Text(emptyStateMessage)
                            .font(.appCallout)
                            .foregroundColor(.onboardingText.opacity(0.7))
                            .padding(.vertical, 20)
                    }

                    if !viewModel.substackSuggestions.isEmpty {
                        OnboardingSuggestionSection(
                            title: "NEWSLETTERS",
                            icon: "envelope.open",
                            items: viewModel.substackSuggestions,
                            isSelected: { viewModel.selectedSourceKeys.contains($0.feedURL ?? "") },
                            onToggle: { viewModel.toggleSource($0) }
                        )
                    }

                    if !viewModel.podcastSuggestions.isEmpty {
                        OnboardingSuggestionSection(
                            title: "PODCASTS",
                            icon: "headphones",
                            items: viewModel.podcastSuggestions,
                            isSelected: { viewModel.selectedSourceKeys.contains($0.feedURL ?? "") },
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
        .accessibilityIdentifier("onboarding.suggestions.screen")
    }

    private var footer: some View {
        VStack(spacing: 10) {
            if !viewModel.isShowingDefaultConfirmation {
                Text("\(viewModel.selectedSourceKeys.count) selected")
                    .font(.appCaption.weight(.semibold))
                    .monospacedDigit()
                    .foregroundColor(.onboardingText.opacity(0.65))
            }

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
                .foregroundColor(.onboardingText.opacity(0.78))
                .buttonStyle(OnboardingTextButtonStyle())
                .accessibilityIdentifier("onboarding.suggestions.retry")
            } else if viewModel.isShowingDefaultConfirmation {
                Button("Personalize instead") {
                    withAnimation(AppMotion.panel) {
                        viewModel.retryPersonalization()
                    }
                }
                .font(.appCallout.weight(.medium))
                .foregroundColor(.onboardingText.opacity(0.78))
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

    private var suggestionsSubtitle: String {
        if viewModel.isShowingDefaultConfirmation {
            return "No searched sources selected yet. You can personalize instead."
        }
        return "Keep the ones that feel right. You can tune this again later."
    }

    private var emptyStateMessage: String {
        if viewModel.isShowingDefaultConfirmation {
            return "No newsletters or podcasts will be added automatically. You can add fast-news sources next."
        }
        return "No matches found yet. You can try again or continue without long-form sources."
    }
}
