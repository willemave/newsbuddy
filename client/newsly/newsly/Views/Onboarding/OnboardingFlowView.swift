//
//  OnboardingFlowView.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import SwiftUI

struct OnboardingFlowView: View {
    @StateObject private var viewModel: OnboardingViewModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private let onFinish: (OnboardingCompleteResponse) -> Void

    init(user: User, onFinish: @escaping (OnboardingCompleteResponse) -> Void) {
        _viewModel = StateObject(wrappedValue: OnboardingViewModel(user: user))
        self.onFinish = onFinish
    }

    var body: some View {
        ZStack {
            WatercolorBackground(energy: 0.15)

            VStack(spacing: 0) {
                progressHeader
                    .padding(.horizontal, 24)
                    .padding(.top, 12)
                    .padding(.bottom, 8)

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
            reduceMotion ? .linear(duration: 0.01) : .spring(response: 0.44, dampingFraction: 0.9),
            value: viewModel.step
        )
        .accessibilityIdentifier("onboarding.screen")
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.step {
        case .intro:
            choiceView
                .transition(screenTransition)
        case .choice:
            choiceView
                .transition(screenTransition)
        case .audio:
            audioView
                .transition(screenTransition)
        case .loading:
            loadingView
                .transition(screenTransition)
        case .suggestions:
            suggestionsView
                .transition(screenTransition)
        }
    }

    private var progressHeader: some View {
        HStack(spacing: 12) {
            Text(currentStepInfo.label.uppercased())
                .font(.editorialMeta)
                .tracking(1.6)
                .foregroundColor(.watercolorSlate.opacity(0.68))

            Spacer(minLength: 0)

            Text("Step \(currentStepInfo.number) of 3")
                .font(.caption.weight(.semibold))
                .monospacedDigit()
                .foregroundColor(.watercolorSlate)
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .background(Capsule().fill(Color.watercolorBase.opacity(0.76)))
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(cardSurface(cornerRadius: 22))
    }

    // MARK: - Choice

    private var choiceView: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 32) {
                Image("Mascot")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 180, height: 180)
                    .shadow(color: .black.opacity(0.08), radius: 18, x: 0, y: 10)
                    .accessibilityLabel("Newsbuddy mascot")

                VStack(spacing: 12) {
                    Text("MEET YOUR GUIDE")
                        .font(.editorialMeta)
                        .tracking(1.8)
                        .foregroundColor(.watercolorSlate.opacity(0.55))
                    Text("Newsbuddy")
                        .font(.watercolorDisplay)
                        .foregroundColor(.watercolorSlate)
                        .multilineTextAlignment(.center)
                    Text("Tell me what you read and I'll round up the good stuff.\nYou can always tune it later.")
                        .font(.watercolorSubtitle)
                        .foregroundColor(.watercolorSlate.opacity(0.74))
                        .multilineTextAlignment(.center)
                        .lineSpacing(3)
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
                    .background(primaryButtonBackground)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboarding.choice.personalized")

                Button {
                    viewModel.chooseDefaults()
                } label: {
                    Text("Skip - use popular defaults")
                        .font(.callout.weight(.medium))
                        .foregroundColor(.watercolorSlate.opacity(0.72))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboarding.choice.defaults")
            }
            .padding(18)
            .background(cardSurface(cornerRadius: 28))

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
            headerBlock(
                eyebrow: "VOICE SETUP",
                title: "Tell us what you read",
                subtitle: "Say a few topics, names, or sources you follow. We'll use that to tune the feed."
            )
            .padding(.top, 24)

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
                .font(.callout.weight(.medium))
                .foregroundColor(.watercolorSlate.opacity(0.72))
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
                .foregroundColor(.watercolorSlate.opacity(0.7))

            if hasTopicPreview {
                topicPreviewCard(
                    eyebrow: "WE HEARD",
                    title: viewModel.topicSummary ?? "Tuning your feed around your interests"
                )
                .padding(.top, 8)
            }
        }
    }

    // MARK: - Loading / Discovery

    private var loadingView: some View {
        VStack(spacing: 0) {
            headerBlock(
                eyebrow: "MATCHING SOURCES",
                title: "Finding your feeds",
                subtitle: "Searching newsletters, podcasts, and Reddit for a strong first set."
            )
            .padding(.top, 24)

            Spacer()

            VStack(spacing: 16) {
                if hasTopicPreview {
                    topicPreviewCard(
                        eyebrow: "PERSONALIZATION",
                        title: viewModel.topicSummary ?? "Tuning your feed around what you said"
                    )
                }

                if viewModel.discoveryLanes.isEmpty {
                    ProgressView()
                        .scaleEffect(1.2)
                        .tint(.watercolorSlate)
                    Text("Preparing search...")
                        .font(.callout)
                        .foregroundColor(.watercolorSlate.opacity(0.7))
                } else {
                    VStack(spacing: 12) {
                        HStack {
                            Text("Live progress")
                                .font(.callout.weight(.semibold))
                                .foregroundColor(.watercolorSlate)

                            Spacer()

                            Text("\(completedLaneCount)/\(viewModel.discoveryLanes.count)")
                                .font(.caption.weight(.semibold))
                                .monospacedDigit()
                                .foregroundColor(.watercolorSlate.opacity(0.7))
                        }

                        ForEach(Array(viewModel.discoveryLanes.enumerated()), id: \.element.id) { index, lane in
                            LaneStatusRow(lane: lane)
                                .animation(
                                    reduceMotion
                                        ? .linear(duration: 0.01)
                                        : .easeOut(duration: 0.36).delay(Double(index) * 0.08),
                                    value: viewModel.discoveryLanes
                                )
                        }
                    }
                    .padding(20)
                    .background(cardSurface(cornerRadius: 24))
                }
            }

            Spacer()

            VStack(spacing: 12) {
                Text(loadingFootnote)
                    .font(.caption)
                    .foregroundColor(.watercolorSlate.opacity(0.62))

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
                    .font(.callout.weight(.medium))
                    .foregroundColor(.watercolorSlate.opacity(0.78))
                    .accessibilityIdentifier("onboarding.loading.retry")
                }

                Button("Use defaults instead") {
                    viewModel.chooseDefaults()
                }
                .font(.callout.weight(.medium))
                .foregroundColor(.watercolorSlate.opacity(0.72))
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
                VStack(alignment: .leading, spacing: 18) {
                    headerBlock(
                        eyebrow: viewModel.isShowingDefaultConfirmation ? "DEFAULT START" : "FINAL PICKS",
                        title: viewModel.isShowingDefaultConfirmation ? "Start with defaults" : "Your picks",
                        subtitle: suggestionsSubtitle,
                        isLeading: true
                    )

                    if hasTopicPreview && !viewModel.isShowingDefaultConfirmation {
                        topicPreviewCard(
                            eyebrow: "TUNED TO",
                            title: viewModel.topicSummary ?? "A feed matched to your interests"
                        )
                    }

                    if viewModel.substackSuggestions.isEmpty
                        && viewModel.podcastSuggestions.isEmpty
                        && viewModel.subredditSuggestions.isEmpty
                    {
                        Text(emptyStateMessage)
                            .font(.callout)
                            .foregroundColor(.watercolorSlate.opacity(0.7))
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
                .padding(.top, 16)
                .padding(.bottom, 128)
            }

            VStack(spacing: 10) {
                if !viewModel.isShowingDefaultConfirmation {
                    Text("\(selectedSuggestionCount) selected")
                        .font(.caption.weight(.semibold))
                        .monospacedDigit()
                        .foregroundColor(.watercolorSlate.opacity(0.65))
                }

                primaryButton(primaryCompletionTitle) {
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
                    .font(.callout.weight(.medium))
                    .foregroundColor(.watercolorSlate.opacity(0.78))
                    .accessibilityIdentifier("onboarding.suggestions.retry")
                } else if viewModel.isShowingDefaultConfirmation {
                    Button("Personalize instead") {
                        withAnimation(.easeInOut(duration: 0.3)) {
                            viewModel.retryPersonalization()
                        }
                    }
                    .font(.callout.weight(.medium))
                    .foregroundColor(.watercolorSlate.opacity(0.78))
                    .accessibilityIdentifier("onboarding.suggestions.personalize")
                }

                if let error = viewModel.errorMessage {
                    Text(error)
                        .font(.caption)
                        .foregroundColor(.red)
                }
            }
            .padding(.horizontal, 24)
            .padding(.top, 14)
            .padding(.bottom, 16)
            .background(
                ZStack(alignment: .top) {
                    Rectangle()
                        .fill(.ultraThinMaterial)

                    LinearGradient(
                        colors: [.clear, Color.watercolorBase.opacity(0.28)],
                        startPoint: .top,
                        endPoint: .bottom
                    )

                    Rectangle()
                        .fill(Color.watercolorSlate.opacity(0.08))
                        .frame(height: 0.5)
                }
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
                    .foregroundColor(.watercolorSlate.opacity(0.55))
                Text(title)
                    .font(.editorialMeta)
                    .foregroundColor(.watercolorSlate.opacity(0.55))
                    .tracking(1.5)

                Spacer()

                Text("\(items.count)")
                    .font(.caption.weight(.semibold))
                    .monospacedDigit()
                    .foregroundColor(.watercolorSlate.opacity(0.68))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Capsule().fill(Color.watercolorSlate.opacity(0.08)))
            }
            .padding(.top, 16)
            .padding(.bottom, 4)

            VStack(spacing: 8) {
                ForEach(items, id: \.stableKey) { suggestion in
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
                .background(primaryButtonBackground)
        }
        .buttonStyle(.plain)
    }

    private func headerBlock(
        eyebrow: String,
        title: String,
        subtitle: String,
        isLeading: Bool = false
    ) -> some View {
        let horizontalAlignment: HorizontalAlignment = isLeading ? .leading : .center
        let textAlignment: TextAlignment = isLeading ? .leading : .center
        let frameAlignment: Alignment = isLeading ? .leading : .center

        return VStack(alignment: horizontalAlignment, spacing: 8) {
            Text(eyebrow)
                .font(.editorialMeta)
                .tracking(1.8)
                .foregroundColor(.watercolorSlate.opacity(0.58))

            Text(title)
                .font(.title2.bold())
                .foregroundColor(.watercolorSlate)
                .multilineTextAlignment(textAlignment)

            Text(subtitle)
                .font(.callout)
                .foregroundColor(.watercolorSlate.opacity(0.72))
                .multilineTextAlignment(textAlignment)
                .lineSpacing(2)
        }
        .frame(maxWidth: .infinity, alignment: frameAlignment)
    }

    private func topicPreviewCard(eyebrow: String, title: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(eyebrow)
                .font(.editorialMeta)
                .tracking(1.6)
                .foregroundColor(.watercolorSlate.opacity(0.58))

            Text(title)
                .font(.callout.weight(.semibold))
                .foregroundColor(.watercolorSlate)
                .fixedSize(horizontal: false, vertical: true)

            if !viewModel.inferredTopics.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Array(viewModel.inferredTopics.prefix(6)), id: \.self) { topic in
                            Text(topic)
                                .font(.caption.weight(.semibold))
                                .foregroundColor(.watercolorSlate)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 8)
                                .background(Capsule().fill(Color.watercolorSlate.opacity(0.08)))
                        }
                    }
                }
            }
        }
        .padding(18)
        .background(cardSurface(cornerRadius: 24))
    }

    private func cardSurface(cornerRadius: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: cornerRadius)
            .fill(Color.watercolorBase.opacity(0.76))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius)
                    .stroke(Color.watercolorSlate.opacity(0.10), lineWidth: 0.5)
            )
            .shadow(color: .black.opacity(0.05), radius: 16, x: 0, y: 10)
    }

    private var primaryButtonBackground: some View {
        RoundedRectangle(cornerRadius: 24)
            .fill(Color.watercolorSlate)
            .shadow(color: .black.opacity(0.10), radius: 18, x: 0, y: 12)
    }

    private var screenTransition: AnyTransition {
        .asymmetric(
            insertion: .opacity.combined(with: .move(edge: .bottom)),
            removal: .opacity.combined(with: .offset(y: -10))
        )
    }

    private var hasTopicPreview: Bool {
        (viewModel.topicSummary?.isEmpty == false) || !viewModel.inferredTopics.isEmpty
    }

    private var completedLaneCount: Int {
        viewModel.discoveryLanes.filter { $0.status == "completed" }.count
    }

    private var currentStepInfo: (number: Int, label: String) {
        switch viewModel.step {
        case .intro, .choice:
            return (1, "Choose your start")
        case .audio, .loading:
            return (2, viewModel.step == .audio ? "Voice setup" : "Matching sources")
        case .suggestions:
            return (3, "Review picks")
        }
    }

    private var selectedSuggestionCount: Int {
        viewModel.selectedSourceKeys.count + viewModel.selectedSubreddits.count
    }

    private var primaryCompletionTitle: String {
        if viewModel.isShowingDefaultConfirmation {
            return "Start with defaults"
        }
        if selectedSuggestionCount > 0 {
            return "Start with \(selectedSuggestionCount) sources"
        }
        return "Start reading"
    }

    private var loadingFootnote: String {
        if !viewModel.discoveryLanes.isEmpty {
            return "\(completedLaneCount) of \(viewModel.discoveryLanes.count) lanes ready"
        }
        return "Usually takes about a minute or two"
    }

    private var suggestionsSubtitle: String {
        if viewModel.isShowingDefaultConfirmation {
            return "Review the defaults or personalize instead."
        }
        return "Keep the ones that feel right. You can tune this again later."
    }

    private var emptyStateMessage: String {
        if viewModel.isShowingDefaultConfirmation {
            return "We'll set up a solid default feed, and you can personalize it later."
        }
        return "No matches found yet. You can try again or continue with defaults."
    }
}
