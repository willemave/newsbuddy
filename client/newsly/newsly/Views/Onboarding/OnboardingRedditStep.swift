//
//  OnboardingRedditStep.swift
//  newsly
//

import SwiftUI

struct OnboardingRedditStep: View {
    let viewModel: OnboardingViewModel

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    onboardingHeaderBlock(
                        eyebrow: "REDDIT",
                        title: "Add subreddit feeds",
                        isLeading: true,
                        titleAccessibilityIdentifier: "onboarding.reddit.screen"
                    )

                    if viewModel.subredditSuggestions.isEmpty {
                        Text("No matches.")
                            .font(.appCallout)
                            .foregroundColor(.onboardingText.opacity(0.7))
                            .padding(.vertical, 20)
                    } else {
                        OnboardingSuggestionSection(
                            title: "SUBREDDITS",
                            items: viewModel.subredditSuggestions,
                            isSelected: { viewModel.isSuggestionSelected($0) },
                            onToggle: { viewModel.toggleSubreddit($0) }
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
            Text("\(viewModel.selectedSubredditSuggestionCount) selected")
                .font(.appCaption.weight(.semibold))
                .monospacedDigit()
                .foregroundColor(.onboardingText.opacity(0.65))

            onboardingPrimaryButton(completionPrimaryTitle) {
                Task { await viewModel.completeOnboarding() }
            }
            .disabled(viewModel.isLoading)
            .accessibilityIdentifier("onboarding.complete")

            Button("Back") {
                withAnimation(AppMotion.panel) {
                    viewModel.returnToAggregators()
                }
            }
            .font(.appCallout.weight(.medium))
            .foregroundColor(.onboardingText.opacity(0.72))
            .buttonStyle(OnboardingTextButtonStyle())
            .accessibilityIdentifier("onboarding.reddit.back")

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

    private var selectedLongformCount: Int {
        viewModel.selectedLongformSuggestionCount
    }

    private var selectedShortformCount: Int {
        viewModel.selectedAggregators.count + viewModel.selectedSubredditSuggestionCount
    }

    private var completionPrimaryTitle: String {
        if selectedShortformCount == 0 {
            return "Start reading"
        }
        return "Start with \(selectedShortformCount + selectedLongformCount) sources"
    }
}
