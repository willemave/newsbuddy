//
//  OnboardingFlowView.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import SwiftUI

struct OnboardingFlowView: View {
    @StateObject private var viewModel: OnboardingViewModel
    private let onFinish: (OnboardingCompleteResponse) -> Void

    init(user: User, onFinish: @escaping (OnboardingCompleteResponse) -> Void) {
        _viewModel = StateObject(wrappedValue: OnboardingViewModel(user: user))
        self.onFinish = onFinish
    }

    var body: some View {
        ZStack {
            WatercolorBackground(energy: 0.15)

            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)

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
        .accessibilityIdentifier("onboarding.screen")
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.step {
        case .intro:
            choiceView
                .transition(.opacity)
        case .choice:
            choiceView
                .transition(.opacity)
        case .audio:
            audioView
                .transition(.opacity)
        case .loading:
            loadingView
                .transition(.opacity)
        case .suggestions:
            suggestionsView
                .transition(.opacity)
        }
    }

    // MARK: - Choice

    private var choiceView: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 28) {
                Image("Mascot")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 180, height: 180)
                    .shadow(color: .black.opacity(0.08), radius: 18, x: 0, y: 10)
                    .accessibilityLabel("Newsbuddy mascot")

                VStack(spacing: 10) {
                    Text("Hi, I'm Newsbuddy")
                        .font(.title.bold())
                        .foregroundColor(.watercolorSlate)
                    Text("Tell me what you read and\nI'll round up the good stuff.")
                        .font(.callout)
                        .foregroundColor(.watercolorSlate.opacity(0.65))
                        .multilineTextAlignment(.center)
                }
            }

            Spacer()

            VStack(spacing: 12) {
                Button {
                    withAnimation(.easeInOut(duration: 0.3)) {
                        viewModel.startPersonalized()
                    }
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "mic.fill")
                            .font(.body.weight(.medium))
                        Text("Personalize with voice")
                            .font(.callout.weight(.semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .foregroundColor(.watercolorBase)
                    .background(Color.watercolorSlate)
                    .clipShape(RoundedRectangle(cornerRadius: 24))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboarding.choice.personalized")

                Button {
                    viewModel.chooseDefaults()
                } label: {
                    Text("Skip — use popular defaults")
                        .font(.callout)
                        .foregroundColor(.watercolorSlate.opacity(0.55))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboarding.choice.defaults")
            }

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.caption)
                    .foregroundColor(.red)
                    .padding(.top, 8)
            }
        }
        .padding(24)
        .padding(.bottom, 16)
        .accessibilityIdentifier("onboarding.choice.screen")
    }

    // MARK: - Audio

    private var audioView: some View {
        VStack(spacing: 0) {
            VStack(spacing: 8) {
                Text("Tell us what you read")
                    .font(.title2.bold())
                    .foregroundColor(.watercolorSlate)
                Text("Just talk — we'll find matching sources.")
                    .font(.callout)
                    .foregroundColor(.watercolorSlate.opacity(0.6))
            }
            .padding(.top, 48)

            Spacer()

            if viewModel.audioState == .transcribing {
                audioProcessingView
            } else {
                OnboardingMicButton(
                    audioState: viewModel.audioState,
                    durationSeconds: viewModel.audioDurationSeconds,
                    onStart: { Task { await viewModel.startAudioCapture() } },
                    onStop: { Task { await viewModel.stopAudioCaptureAndDiscover() } }
                )
            }

            Spacer()

            if viewModel.audioState != .transcribing {
                Button("Skip") {
                    viewModel.chooseDefaults()
                }
                .font(.callout)
                .foregroundColor(.watercolorSlate.opacity(0.5))
                .padding(.bottom, 16)
                .accessibilityIdentifier("onboarding.audio.skip")
            }

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.caption)
                    .foregroundColor(.red)
                    .padding(.bottom, 8)
            }
        }
        .padding(.horizontal, 24)
        .task {
            await viewModel.startAudioCaptureIfNeeded()
        }
        .accessibilityIdentifier("onboarding.audio.screen")
    }

    private var audioProcessingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
                .tint(.watercolorSlate)
            Text("Processing your interests...")
                .font(.callout)
                .foregroundColor(.watercolorSlate.opacity(0.6))
        }
    }

    // MARK: - Loading / Discovery

    private var loadingView: some View {
        VStack(spacing: 0) {
            VStack(spacing: 8) {
                Text("Finding your feeds")
                    .font(.title2.bold())
                    .foregroundColor(.watercolorSlate)
                Text("Searching newsletters, podcasts, and Reddit")
                    .font(.callout)
                    .foregroundColor(.watercolorSlate.opacity(0.6))
            }
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.top, 48)

            Spacer()

            VStack(spacing: 16) {
                if viewModel.discoveryLanes.isEmpty {
                    ProgressView()
                        .scaleEffect(1.2)
                        .tint(.watercolorSlate)
                    Text("Preparing search...")
                        .font(.callout)
                        .foregroundColor(.watercolorSlate.opacity(0.6))
                } else {
                    VStack(spacing: 12) {
                        ForEach(viewModel.discoveryLanes) { lane in
                            LaneStatusRow(lane: lane)
                        }
                    }
                    .padding(20)
                    .glassCard(cornerRadius: 20)
                }
            }

            Spacer()

            VStack(spacing: 12) {
                Text("Usually takes one to two minutes")
                    .font(.caption)
                    .foregroundColor(.watercolorSlate.opacity(0.5))

                if let message = viewModel.discoveryErrorMessage {
                    Text(message)
                        .font(.caption)
                        .foregroundColor(.orange)
                }

                if viewModel.shouldOfferContinueWaiting {
                    Button("Keep waiting") {
                        viewModel.continueWaitingForDiscovery()
                    }
                    .font(.callout.weight(.semibold))
                    .foregroundColor(.watercolorSlate)
                    .accessibilityIdentifier("onboarding.loading.keep_waiting")
                }

                if viewModel.shouldOfferRetryFromLoading {
                    Button("Try again") {
                        withAnimation(.easeInOut(duration: 0.3)) {
                            viewModel.retryPersonalization()
                        }
                    }
                    .font(.callout)
                    .foregroundColor(.watercolorSlate.opacity(0.7))
                    .accessibilityIdentifier("onboarding.loading.retry")
                }

                Button("Use defaults instead") {
                    viewModel.chooseDefaults()
                }
                .font(.callout)
                .foregroundColor(.watercolorSlate.opacity(0.5))
                .accessibilityIdentifier("onboarding.loading.use_defaults")
            }
            .padding(.bottom, 16)
        }
        .padding(.horizontal, 24)
        .accessibilityIdentifier("onboarding.loading.screen")
    }

    // MARK: - Suggestions

    private var suggestionsView: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(viewModel.isShowingDefaultConfirmation ? "Start with defaults" : "Your picks")
                            .font(.title2.bold())
                            .foregroundColor(.watercolorSlate)
                        Text(suggestionsSubtitle)
                            .font(.callout)
                            .foregroundColor(.watercolorSlate.opacity(0.6))
                    }
                    .padding(.bottom, 20)

                    if viewModel.substackSuggestions.isEmpty && viewModel.podcastSuggestions.isEmpty && viewModel.subredditSuggestions.isEmpty {
                        Text(emptyStateMessage)
                            .font(.callout)
                            .foregroundColor(.watercolorSlate.opacity(0.6))
                            .padding(.vertical, 20)
                    }

                    if !viewModel.substackSuggestions.isEmpty {
                        suggestionSection(
                            title: "NEWSLETTERS",
                            icon: "envelope.open",
                            items: viewModel.substackSuggestions,
                            isSelected: { viewModel.selectedSourceKeys.contains($0.feedURL ?? "") },
                            onToggle: { viewModel.toggleSource($0) }
                        )
                    }

                    if !viewModel.podcastSuggestions.isEmpty {
                        suggestionSection(
                            title: "PODCASTS",
                            icon: "headphones",
                            items: viewModel.podcastSuggestions,
                            isSelected: { viewModel.selectedSourceKeys.contains($0.feedURL ?? "") },
                            onToggle: { viewModel.toggleSource($0) }
                        )
                    }

                    if !viewModel.subredditSuggestions.isEmpty {
                        suggestionSection(
                            title: "REDDIT",
                            icon: "bubble.left.and.text.bubble.right",
                            items: viewModel.subredditSuggestions,
                            isSelected: { viewModel.selectedSubreddits.contains($0.subreddit ?? "") },
                            onToggle: { viewModel.toggleSubreddit($0) }
                        )
                    }
                }
                .padding(.horizontal, 24)
                .padding(.top, 24)
                .padding(.bottom, 100)
            }

            // Sticky bottom button
            VStack(spacing: 8) {
                primaryButton(viewModel.isShowingDefaultConfirmation ? "Start with defaults" : "Start reading") {
                    Task { await viewModel.completeOnboarding() }
                }
                .disabled(viewModel.isLoading)
                .accessibilityIdentifier("onboarding.complete")

                if viewModel.shouldOfferRetryFromSuggestions {
                    Button("Try again") {
                        withAnimation(.easeInOut(duration: 0.3)) {
                            viewModel.retryPersonalization()
                        }
                    }
                    .font(.callout)
                    .foregroundColor(.watercolorSlate.opacity(0.7))
                    .accessibilityIdentifier("onboarding.suggestions.retry")
                } else if viewModel.isShowingDefaultConfirmation {
                    Button("Personalize instead") {
                        withAnimation(.easeInOut(duration: 0.3)) {
                            viewModel.retryPersonalization()
                        }
                    }
                    .font(.callout)
                    .foregroundColor(.watercolorSlate.opacity(0.7))
                    .accessibilityIdentifier("onboarding.suggestions.personalize")
                }

                if let error = viewModel.errorMessage {
                    Text(error)
                        .font(.caption)
                        .foregroundColor(.red)
                }
            }
            .padding(.horizontal, 24)
            .padding(.top, 12)
            .padding(.bottom, 16)
            .background(
                LinearGradient(
                    colors: [.clear, Color.watercolorBase.opacity(0.8), Color.watercolorBase],
                    startPoint: .top,
                    endPoint: .bottom
                )
                .ignoresSafeArea(edges: .bottom)
            )
        }
        .accessibilityIdentifier("onboarding.suggestions.screen")
    }

    private func suggestionSection(
        title: String,
        icon: String,
        items: [OnboardingSuggestion],
        isSelected: @escaping (OnboardingSuggestion) -> Bool,
        onToggle: @escaping (OnboardingSuggestion) -> Void
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: icon)
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundColor(.watercolorSlate.opacity(0.5))
                Text(title)
                    .font(.editorialMeta)
                    .foregroundColor(.watercolorSlate.opacity(0.5))
                    .tracking(1.5)
            }
            .padding(.top, 16)
            .padding(.bottom, 4)

            VStack(spacing: 8) {
                ForEach(Array(items.enumerated()), id: \.element.stableKey) { _, suggestion in
                    OnboardingSuggestionCard(
                        suggestion: suggestion,
                        isSelected: isSelected(suggestion),
                        onToggle: { onToggle(suggestion) }
                    )
                }
            }
        }
    }

    // MARK: - Shared Components

    private func primaryButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.callout.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .foregroundColor(.watercolorBase)
                .background(Color.watercolorSlate)
                .clipShape(RoundedRectangle(cornerRadius: 24))
        }
        .buttonStyle(.plain)
    }

    private var suggestionsSubtitle: String {
        if viewModel.isShowingDefaultConfirmation {
            return "Review the defaults or personalize instead."
        }
        return "Deselect any you don't want."
    }

    private var emptyStateMessage: String {
        if viewModel.isShowingDefaultConfirmation {
            return "We'll set up a solid default feed, and you can personalize it later."
        }
        return "No matches found yet. You can try again or continue with defaults."
    }
}
