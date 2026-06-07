//
//  DiscoveryPersonalizeSheet.swift
//  newsly
//
//  Voice personalization sheet presented from the Discover tab.
//

import SwiftUI

struct DiscoveryPersonalizeSheet: View {
    @StateObject private var viewModel: DiscoveryPersonalizeViewModel
    @Environment(\.dismiss) private var dismiss
    private let onComplete: () -> Void

    init(userId: Int, onComplete: @escaping () -> Void) {
        _viewModel = StateObject(
            wrappedValue: DiscoveryPersonalizeViewModel(userId: userId)
        )
        self.onComplete = onComplete
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
        .onAppear {
            viewModel.onComplete = { [dismiss, onComplete] in
                dismiss()
                onComplete()
            }
            Task { await viewModel.resumeDiscoveryIfNeeded() }
        }
        .onDisappear {
            viewModel.cancelPersonalization()
        }
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.step {
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

    // MARK: - Audio Step

    private var audioView: some View {
        VStack(spacing: 0) {
            sheetHeader(
                title: "Personalize your feeds",
                subtitle: "Tell us your interests in 30 seconds."
            )

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

            VStack(spacing: 12) {
                Button("Skip") {
                    viewModel.skipPersonalization()
                }
                .font(.appCallout)
                .foregroundColor(.onboardingText.opacity(0.5))

                Button("Cancel") {
                    viewModel.cancelPersonalization()
                    dismiss()
                }
                .font(.appCallout)
                .foregroundColor(.onboardingText.opacity(0.4))
            }
            .padding(.bottom, 16)

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.appCaption)
                    .foregroundColor(.statusDestructive)
                    .padding(.bottom, 8)
            }
        }
        .padding(.horizontal, 24)
        .task {
            await viewModel.startAudioCaptureIfNeeded()
        }
    }

    private var audioProcessingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
                .tint(.onboardingText)
            Text("Processing your interests...")
                .font(.appCallout)
                .foregroundColor(.onboardingText.opacity(0.6))
        }
    }

    // MARK: - Loading Step

    private var loadingView: some View {
        VStack(spacing: 0) {
            sheetHeader(
                title: "Finding your feeds",
                subtitle: "Searching newsletters, podcasts, and Reddit."
            )

            Spacer()

            VStack(spacing: 16) {
                if viewModel.discoveryLanes.isEmpty {
                    ProgressView()
                        .scaleEffect(1.2)
                        .tint(.onboardingText)
                    Text("Preparing search...")
                        .font(.appCallout)
                        .foregroundColor(.onboardingText.opacity(0.6))
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
                Text("Usually takes under a minute")
                    .font(.appCaption)
                    .foregroundColor(.onboardingText.opacity(0.5))

                if let message = viewModel.discoveryErrorMessage {
                    Text(message)
                        .font(.appCaption)
                        .foregroundColor(.brandPrimary)
                }

                Button("Cancel") {
                    viewModel.cancelPersonalization()
                    dismiss()
                }
                .font(.appCallout)
                .foregroundColor(.onboardingText.opacity(0.4))
            }
            .padding(.bottom, 16)
        }
        .padding(.horizontal, 24)
    }

    // MARK: - Suggestions Step

    private var suggestionsView: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Your picks")
                            .font(.appTitle2.bold())
                            .foregroundColor(.onboardingText)
                        Text("Tap to deselect any you don't want.")
                            .font(.appCallout)
                            .foregroundColor(.onboardingText.opacity(0.6))
                    }
                    .padding(.bottom, 20)

                    if viewModel.substackSuggestions.isEmpty
                        && viewModel.podcastSuggestions.isEmpty
                        && viewModel.subredditSuggestions.isEmpty
                    {
                        Text("No matches found. Try again to add searched sources.")
                            .font(.appCallout)
                            .foregroundColor(.onboardingText.opacity(0.6))
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

            // Sticky bottom
            VStack(spacing: 8) {
                Button {
                    Task { await viewModel.completePersonalization() }
                } label: {
                    Text("Add to my feeds")
                        .font(.appCallout.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .foregroundColor(.onboardingSurface)
                        .background(Color.onboardingText)
                        .clipShape(RoundedRectangle(cornerRadius: 24))
                }
                .buttonStyle(.plain)
                .disabled(viewModel.isLoading)

                Button("Cancel") {
                    viewModel.cancelPersonalization()
                    dismiss()
                }
                .font(.appCallout)
                .foregroundColor(.onboardingText.opacity(0.4))

                if let error = viewModel.errorMessage {
                    Text(error)
                        .font(.appCaption)
                        .foregroundColor(.statusDestructive)
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 16)
            .glassCard(cornerRadius: 0)
        }
    }

    // MARK: - Shared Helpers

    private func sheetHeader(title: String, subtitle: String) -> some View {
        VStack(spacing: 8) {
            Text(title)
                .font(.appTitle2.bold())
                .foregroundColor(.onboardingText)
            Text(subtitle)
                .font(.appCallout)
                .foregroundColor(.onboardingText.opacity(0.6))
        }
        .padding(.top, 48)
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
                    .font(.appSymbol(size: 9, weight: .semibold))
                    .foregroundColor(.onboardingText.opacity(0.5))
                Text(title)
                    .font(.editorialMeta)
                    .foregroundColor(.onboardingText.opacity(0.5))
                    .tracking(1.5)
            }
            .padding(.top, 16)
            .padding(.bottom, 4)

            VStack(spacing: 6) {
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
}
