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
                        subtitle: "Optional. Headline feeds that are quick to skim.",
                        isLeading: true,
                        titleAccessibilityIdentifier: "onboarding.aggregators.screen"
                    )

                    OnboardingAggregatorSection(
                        selectedAggregators: viewModel.selectedAggregators,
                        selectedAggregatorTopics: viewModel.selectedAggregatorTopics,
                        reduceMotion: reduceMotion,
                        onToggleAggregator: viewModel.toggleAggregator,
                        onToggleAggregatorTopic: viewModel.toggleAggregatorTopic
                    )
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
            Text("\(viewModel.selectedAggregators.count) selected")
                .font(.appCaption.weight(.semibold))
                .monospacedDigit()
                .foregroundColor(.onSurfaceSecondary)

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
            .foregroundColor(.onSurfaceSecondary)
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
    let selectedAggregatorTopics: [String: Set<String>]
    let reduceMotion: Bool
    let onToggleAggregator: (OnboardingAggregatorOption) -> Void
    let onToggleAggregatorTopic: (_ topic: String, _ aggregatorKey: String) -> Void

    var body: some View {
        VStack(spacing: 8) {
            ForEach(onboardingAggregatorOptions) { option in
                aggregatorRow(option: option)
            }
        }
    }

    private func aggregatorRow(option: OnboardingAggregatorOption) -> some View {
        let isSelected = selectedAggregators.contains(option.key)
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
                            .foregroundColor(.onSurfaceSecondary)
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

            if isSelected && !option.topics.isEmpty {
                topicChips(for: option)
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

    private func topicChips(for option: OnboardingAggregatorOption) -> some View {
        let selectedTopics = selectedAggregatorTopics[option.key] ?? []
        return VStack(alignment: .leading, spacing: 8) {
            Text("TOPICS")
                .font(.editorialMeta)
                .tracking(1.4)
                .foregroundColor(.onSurfaceTertiary)

            FlowLayout(spacing: 6) {
                ForEach(option.topics) { topic in
                    let isOn = selectedTopics.contains(topic.value)
                    Button {
                        onToggleAggregatorTopic(topic.value, option.key)
                    } label: {
                        Text(topic.label)
                            .font(.appCaption.weight(.semibold))
                            .foregroundColor(
                                isOn
                                    ? Color.onboardingText
                                    : Color.onSurfaceSecondary
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
                    .accessibilityIdentifier("onboarding.fastnews.\(option.key).topic.\(topic.value)")
                }
            }
        }
    }
}
