//
//  OnboardingLoadingStep.swift
//  newsly
//

import SwiftUI

struct OnboardingLoadingStep: View {
    let viewModel: OnboardingViewModel
    let reduceMotion: Bool

    var body: some View {
        VStack(spacing: 0) {
            onboardingHeaderBlock(
                eyebrow: "MATCHING SOURCES",
                title: "Finding your feeds",
                titleAccessibilityIdentifier: "onboarding.loading.screen"
            )
            .padding(.top, 24)

            Spacer()

            VStack(spacing: 16) {
                if viewModel.discoveryLanes.isEmpty {
                    ProgressView()
                        .scaleEffect(1.2)
                        .tint(.onboardingText)
                    Text("Preparing search...")
                        .font(.appCallout)
                        .foregroundColor(.onboardingText.opacity(0.7))
                } else {
                    VStack(spacing: 6) {
                        ForEach(Array(viewModel.discoveryLanes.enumerated()), id: \.element.id) { index, lane in
                            LaneStatusRow(lane: lane)
                                .animation(
                                    laneEntranceAnimation(index: index),
                                    value: viewModel.discoveryLanes
                                )

                            if index < viewModel.discoveryLanes.count - 1 || isFinalizingLanes {
                                Rectangle()
                                    .fill(Color.onboardingText.opacity(0.06))
                                    .frame(height: 0.5)
                            }
                        }

                        if isFinalizingLanes {
                            finalizingRow
                                .transition(.opacity.combined(with: .move(edge: .top)))
                        }
                    }
                    .padding(20)
                    .background(cardSurface(cornerRadius: 24))
                    .animation(
                        AppMotion.respectingReduceMotion(reduceMotion, AppMotion.panel),
                        value: isFinalizingLanes
                    )
                }
            }

            Spacer()

            loadingFooter
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
    }

    private var loadingFooter: some View {
        VStack(spacing: 14) {
            if let loadingFootnote {
                Text(loadingFootnote)
                    .font(.appCaption)
                    .foregroundColor(.onboardingText.opacity(0.62))
            }

            if let message = viewModel.discoveryErrorMessage {
                discoveryErrorView(message)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            }

            if viewModel.shouldOfferContinueWaiting {
                Button {
                    viewModel.continueWaitingForDiscovery()
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "hourglass")
                            .font(.appSymbol(size: 14, weight: .semibold))
                        Text("Keep waiting")
                            .font(.appCallout.weight(.semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .foregroundColor(.onboardingSurface)
                    .background(primaryButtonBackground)
                }
                .buttonStyle(OnboardingPrimaryPressStyle())
                .accessibilityIdentifier("onboarding.loading.keep_waiting")
                .transition(.opacity.combined(with: .scale(scale: 0.96)))
            }

            if viewModel.shouldOfferRetryFromLoading {
                Button {
                    withAnimation(AppMotion.panel) {
                        viewModel.retryPersonalization()
                    }
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "arrow.counterclockwise")
                            .font(.appSymbol(size: 13, weight: .semibold))
                        Text("Try again")
                            .font(.appCallout.weight(.semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 13)
                    .foregroundColor(.onboardingText)
                    .background(
                        RoundedRectangle(cornerRadius: 22, style: .continuous)
                            .fill(Color.onboardingText.opacity(0.08))
                            .overlay(
                                RoundedRectangle(cornerRadius: 22, style: .continuous)
                                    .stroke(Color.onboardingText.opacity(0.14), lineWidth: 0.5)
                            )
                    )
                }
                .buttonStyle(OnboardingPrimaryPressStyle())
                .accessibilityIdentifier("onboarding.loading.retry")
                .transition(.opacity.combined(with: .scale(scale: 0.96)))
            }

            Button("Skip personalization") {
                viewModel.chooseDefaults()
            }
            .font(.appFootnote.weight(.medium))
            .foregroundColor(.onboardingText.opacity(0.6))
            .buttonStyle(OnboardingTextButtonStyle())
            .accessibilityIdentifier("onboarding.loading.skip_personalization")
            .padding(.top, 2)
        }
        .padding(.bottom, 8)
        .animation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.emphasized),
            value: viewModel.discoveryErrorMessage
        )
        .animation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.emphasized),
            value: viewModel.shouldOfferContinueWaiting
        )
        .animation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.emphasized),
            value: viewModel.shouldOfferRetryFromLoading
        )
    }

    private func discoveryErrorView(_ message: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "clock.arrow.circlepath")
                .font(.appSymbol(size: 13, weight: .semibold))
                .foregroundColor(.onboardingText.opacity(0.78))
                .padding(.top, 1)
            Text(message)
                .font(.appFootnote)
                .foregroundColor(.onboardingText.opacity(0.84))
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Color.statusDestructive.opacity(0.12))
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(Color.statusDestructive.opacity(0.28), lineWidth: 0.5)
                )
        )
    }

    private var completedLaneCount: Int {
        viewModel.discoveryLanes.filter { $0.status == "completed" }.count
    }

    private var isFinalizingLanes: Bool {
        !viewModel.discoveryLanes.isEmpty
            && completedLaneCount == viewModel.discoveryLanes.count
    }

    private var finalizingRow: some View {
        HStack(alignment: .center, spacing: 12) {
            ZStack {
                Circle()
                    .fill(Color.onboardingSelectionAccent.opacity(0.18))
                    .frame(width: 26, height: 26)

                FinalizingSparkle()
            }

            VStack(alignment: .leading, spacing: 1) {
                Text("Finalizing")
                    .font(.appCallout.weight(.medium))
                    .foregroundColor(.onboardingText.opacity(0.95))
                Text("Shaping your first picks")
                    .font(.appCaption)
                    .foregroundColor(.onboardingText.opacity(0.55))
            }

            Spacer()
        }
        .padding(.vertical, 4)
    }

    private var loadingFootnote: String? {
        if viewModel.discoveryLanes.isEmpty {
            return "Usually takes about a minute or two"
        }
        return nil
    }

    private func laneEntranceAnimation(index: Int) -> Animation {
        AppMotion.respectingReduceMotion(
            reduceMotion,
            AppMotion.emphasized.delay(Double(index) * 0.06)
        )
    }
}

private struct FinalizingSparkle: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var breathing = false

    var body: some View {
        Image(systemName: "sparkles")
            .font(.appSymbol(size: 12, weight: .semibold))
            .foregroundStyle(Color.onboardingSelectionAccent)
            .scaleEffect(breathing ? 1.12 : 0.92)
            .opacity(breathing ? 1.0 : 0.78)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(AppMotion.finalizingPulse) {
                    breathing = true
                }
            }
            .onChange(of: reduceMotion) { _, reduceMotion in
                if reduceMotion {
                    breathing = false
                } else {
                    withAnimation(AppMotion.finalizingPulse) {
                        breathing = true
                    }
                }
            }
    }
}
