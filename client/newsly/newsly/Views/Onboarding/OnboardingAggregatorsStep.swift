//
//  OnboardingAggregatorsStep.swift
//  newsly
//

import SwiftUI

struct OnboardingAggregatorsStep: View {
    let viewModel: OnboardingViewModel
    let reduceMotion: Bool

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    onboardingHeaderBlock(
                        eyebrow: "FAST NEWS",
                        title: "Add news aggregators",
                        subtitle: "Broad headline streams across tech, science, finance, politics, and media.",
                        isLeading: true
                    )

                    OnboardingAggregatorSection(
                        selectedAggregators: viewModel.selectedAggregators,
                        selectedBrutalistTopics: viewModel.selectedBrutalistTopics,
                        reduceMotion: reduceMotion,
                        onToggleAggregator: viewModel.toggleAggregator,
                        onToggleBrutalistTopic: viewModel.toggleBrutalistTopic
                    )
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 16)
                .padding(.bottom, 128)
            }

            footer
        }
        .accessibilityIdentifier("onboarding.aggregators.screen")
    }

    private var footer: some View {
        VStack(spacing: 10) {
            Text("\(viewModel.selectedAggregators.count) selected")
                .font(.appCaption.weight(.semibold))
                .monospacedDigit()
                .foregroundColor(.onboardingText.opacity(0.65))

            onboardingPrimaryButton("Continue") {
                withAnimation(AppMotion.panel) {
                    viewModel.advanceToReddit()
                }
            }
            .disabled(viewModel.isLoading)
            .accessibilityIdentifier("onboarding.aggregators.continue")

            Button("Back") {
                withAnimation(AppMotion.panel) {
                    viewModel.returnToSuggestions()
                }
            }
            .font(.appCallout.weight(.medium))
            .foregroundColor(.onboardingText.opacity(0.72))
            .buttonStyle(OnboardingTextButtonStyle())
            .accessibilityIdentifier("onboarding.aggregators.back")

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
}

private struct OnboardingAggregatorSection: View {
    let selectedAggregators: Set<String>
    let selectedBrutalistTopics: Set<String>
    let reduceMotion: Bool
    let onToggleAggregator: (OnboardingAggregatorOption) -> Void
    let onToggleBrutalistTopic: (String) -> Void

    var body: some View {
        VStack(spacing: 8) {
            ForEach(onboardingAggregatorOptions) { option in
                aggregatorRow(option: option)
            }
        }
    }

    private func aggregatorRow(option: OnboardingAggregatorOption) -> some View {
        let isSelected = selectedAggregators.contains(option.key)
        let isBrutalist = option.key == "brutalist"
        return VStack(alignment: .leading, spacing: 10) {
            Button {
                onToggleAggregator(option)
            } label: {
                HStack(spacing: 12) {
                    ZStack {
                        Circle()
                            .fill(Color.onboardingText.opacity(isSelected ? 0.16 : 0.08))
                            .frame(width: 36, height: 36)
                        Image(systemName: option.icon)
                            .font(.appSymbol(size: 15, weight: .medium))
                            .foregroundColor(.onboardingText)
                    }

                    VStack(alignment: .leading, spacing: 2) {
                        Text(option.title)
                            .font(.appCallout.weight(.semibold))
                            .foregroundColor(.onboardingText)
                        Text(option.subtitle)
                            .font(.appCaption)
                            .foregroundColor(.onboardingText.opacity(0.62))
                            .lineLimit(2)
                    }

                    Spacer()

                    OnboardingSelectionDot(isSelected: isSelected)
                }
                .padding(12)
                .background(
                    RoundedRectangle(cornerRadius: 18)
                        .fill(Color.onboardingSurface.opacity(isSelected ? 0.92 : 0.7))
                        .overlay(
                            RoundedRectangle(cornerRadius: 18)
                                .stroke(
                                    isSelected
                                        ? Color.onboardingSelectionAccent.opacity(0.4)
                                        : Color.onboardingText.opacity(0.10),
                                    lineWidth: isSelected ? 1 : 0.5
                                )
                        )
                )
            }
            .buttonStyle(OnboardingTextButtonStyle())
            .accessibilityIdentifier("onboarding.fastnews.aggregator.\(option.key)")

            if isBrutalist && isSelected {
                brutalistTopicChips
                    .padding(.leading, 48)
                    .padding(.trailing, 12)
                    .padding(.bottom, 4)
                    .transition(.opacity)
            }
        }
        .animation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.subtle),
            value: isSelected
        )
    }

    private var brutalistTopicChips: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("TOPICS")
                .font(.editorialMeta)
                .tracking(1.4)
                .foregroundColor(.onboardingText.opacity(0.55))

            FlowLayout(spacing: 6) {
                ForEach(onboardingBrutalistTopics, id: \.self) { topic in
                    let isOn = selectedBrutalistTopics.contains(topic)
                    Button {
                        onToggleBrutalistTopic(topic)
                    } label: {
                        Text(topic.capitalized)
                            .font(.appCaption.weight(.semibold))
                            .foregroundColor(
                                isOn
                                    ? Color.onboardingText.opacity(0.95)
                                    : Color.onboardingText.opacity(0.62)
                            )
                            .padding(.horizontal, 11)
                            .padding(.vertical, 6)
                            .background(
                                Capsule(style: .continuous)
                                    .fill(
                                        isOn
                                            ? Color.onboardingSelectionAccent.opacity(0.22)
                                            : Color.clear
                                    )
                                    .overlay(
                                        Capsule(style: .continuous)
                                            .strokeBorder(
                                                isOn
                                                    ? Color.onboardingSelectionAccent.opacity(0.4)
                                                    : Color.onboardingText.opacity(0.18),
                                                lineWidth: 0.75
                                            )
                                    )
                            )
                    }
                    .buttonStyle(OnboardingTextButtonStyle())
                    .accessibilityIdentifier("onboarding.fastnews.brutalist.topic.\(topic)")
                }
            }
        }
    }
}
