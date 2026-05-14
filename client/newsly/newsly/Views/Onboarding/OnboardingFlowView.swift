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
                    .padding(.horizontal, 40)
                    .padding(.top, 14)
                    .padding(.bottom, 4)

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
        case .fastNews:
            fastNewsView
                .transition(screenTransition)
        }
    }

    private var progressHeader: some View {
        HStack(spacing: 6) {
            ForEach(0..<progressStepTotal, id: \.self) { index in
                Capsule()
                    .fill(
                        index < currentStepInfo.number
                            ? Color.watercolorSlate.opacity(0.55)
                            : Color.watercolorSlate.opacity(0.14)
                    )
                    .frame(height: 4)
            }
        }
        .animation(
            reduceMotion ? .linear(duration: 0.01) : .spring(response: 0.4, dampingFraction: 0.9),
            value: currentStepInfo.number
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            "Step \(currentStepInfo.number) of \(progressStepTotal), \(currentStepInfo.label)"
        )
    }

    private var progressStepTotal: Int { 4 }

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
                    Text("I'm going to help you get onboarded.\nLet's get going.")
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
                .buttonStyle(OnboardingPrimaryPressStyle())
                .accessibilityIdentifier("onboarding.choice.personalized")

                Button {
                    viewModel.chooseDefaults()
                } label: {
                    Text("Skip - use popular defaults")
                        .font(.callout.weight(.medium))
                        .foregroundColor(.watercolorSlate.opacity(0.72))
                }
                .buttonStyle(OnboardingTextButtonStyle())
                .accessibilityIdentifier("onboarding.choice.defaults")
            }
            .padding(12)
            .background(cardSurface(cornerRadius: 36))

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
                .buttonStyle(OnboardingTextButtonStyle())
                .padding(.bottom, 8)
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
                if viewModel.discoveryLanes.isEmpty {
                    ProgressView()
                        .scaleEffect(1.2)
                        .tint(.watercolorSlate)
                    Text("Preparing search...")
                        .font(.callout)
                        .foregroundColor(.watercolorSlate.opacity(0.7))
                } else {
                    VStack(spacing: 14) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text("LIVE PROGRESS")
                                .font(.editorialMeta)
                                .tracking(1.6)
                                .foregroundColor(.watercolorSlate.opacity(0.55))

                            Spacer()

                            HStack(spacing: 3) {
                                Text("\(completedLaneCount)")
                                    .font(.footnote.weight(.semibold))
                                    .monospacedDigit()
                                    .foregroundColor(.watercolorSlate.opacity(0.78))
                                Text("/\(viewModel.discoveryLanes.count)")
                                    .font(.caption.weight(.medium))
                                    .monospacedDigit()
                                    .foregroundColor(.watercolorSlate.opacity(0.42))
                            }
                            .contentTransition(.numericText())
                        }

                        VStack(spacing: 6) {
                            ForEach(Array(viewModel.discoveryLanes.enumerated()), id: \.element.id) { index, lane in
                                LaneStatusRow(lane: lane)
                                    .animation(
                                        reduceMotion
                                            ? .linear(duration: 0.01)
                                            : .easeOut(duration: 0.36).delay(Double(index) * 0.08),
                                        value: viewModel.discoveryLanes
                                    )

                                if index < viewModel.discoveryLanes.count - 1 || isFinalizingLanes {
                                    Rectangle()
                                        .fill(Color.watercolorSlate.opacity(0.06))
                                        .frame(height: 0.5)
                                }
                            }

                            if isFinalizingLanes {
                                finalizingRow
                                    .transition(.opacity.combined(with: .move(edge: .top)))
                            }
                        }
                    }
                    .padding(20)
                    .background(cardSurface(cornerRadius: 24))
                    .animation(
                        reduceMotion ? .linear(duration: 0.01) : .easeInOut(duration: 0.3),
                        value: isFinalizingLanes
                    )
                }
            }

            Spacer()

            VStack(spacing: 14) {
                Text(loadingFootnote)
                    .font(.caption)
                    .foregroundColor(.watercolorSlate.opacity(0.62))

                if let message = viewModel.discoveryErrorMessage {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "clock.arrow.circlepath")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(.watercolorSlate.opacity(0.78))
                            .padding(.top, 1)
                        Text(message)
                            .font(.footnote)
                            .foregroundColor(.watercolorSlate.opacity(0.84))
                            .multilineTextAlignment(.leading)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 11)
                    .background(
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .fill(Color.watercolorDiffusedPeach.opacity(0.18))
                            .overlay(
                                RoundedRectangle(cornerRadius: 16, style: .continuous)
                                    .stroke(Color.watercolorDiffusedPeach.opacity(0.32), lineWidth: 0.5)
                            )
                    )
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                }

                if viewModel.shouldOfferContinueWaiting {
                    Button {
                        viewModel.continueWaitingForDiscovery()
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "hourglass")
                                .font(.system(size: 14, weight: .semibold))
                            Text("Keep waiting")
                                .font(.callout.weight(.semibold))
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .foregroundColor(.watercolorBase)
                        .background(primaryButtonBackground)
                    }
                    .buttonStyle(OnboardingPrimaryPressStyle())
                    .accessibilityIdentifier("onboarding.loading.keep_waiting")
                    .transition(.opacity.combined(with: .scale(scale: 0.96)))
                }

                if viewModel.shouldOfferRetryFromLoading {
                    Button {
                        withAnimation(.easeInOut(duration: 0.3)) {
                            viewModel.retryPersonalization()
                        }
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "arrow.counterclockwise")
                                .font(.system(size: 13, weight: .semibold))
                            Text("Try again")
                                .font(.callout.weight(.semibold))
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 13)
                        .foregroundColor(.watercolorSlate)
                        .background(
                            RoundedRectangle(cornerRadius: 22, style: .continuous)
                                .fill(Color.watercolorSlate.opacity(0.08))
                                .overlay(
                                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                                        .stroke(Color.watercolorSlate.opacity(0.14), lineWidth: 0.5)
                                )
                        )
                    }
                    .buttonStyle(OnboardingPrimaryPressStyle())
                    .accessibilityIdentifier("onboarding.loading.retry")
                    .transition(.opacity.combined(with: .scale(scale: 0.96)))
                }

                Button("Use defaults instead") {
                    viewModel.chooseDefaults()
                }
                .font(.footnote.weight(.medium))
                .foregroundColor(.watercolorSlate.opacity(0.6))
                .buttonStyle(OnboardingTextButtonStyle())
                .accessibilityIdentifier("onboarding.loading.use_defaults")
                .padding(.top, 2)
            }
            .padding(.bottom, 8)
            .animation(
                reduceMotion ? .linear(duration: 0.01) : .spring(response: 0.42, dampingFraction: 0.86),
                value: viewModel.discoveryErrorMessage
            )
            .animation(
                reduceMotion ? .linear(duration: 0.01) : .spring(response: 0.42, dampingFraction: 0.86),
                value: viewModel.shouldOfferContinueWaiting
            )
            .animation(
                reduceMotion ? .linear(duration: 0.01) : .spring(response: 0.42, dampingFraction: 0.86),
                value: viewModel.shouldOfferRetryFromLoading
            )
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

                    if viewModel.substackSuggestions.isEmpty
                        && viewModel.podcastSuggestions.isEmpty
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
                }
                .padding(.horizontal, 24)
                .padding(.top, 16)
                .padding(.bottom, 128)
            }

            VStack(spacing: 10) {
                if !viewModel.isShowingDefaultConfirmation {
                    Text("\(selectedLongformCount) selected")
                        .font(.caption.weight(.semibold))
                        .monospacedDigit()
                        .foregroundColor(.watercolorSlate.opacity(0.65))
                }

                primaryButton(suggestionsPrimaryTitle) {
                    withAnimation(.easeInOut(duration: 0.3)) {
                        viewModel.advanceToFastNews()
                    }
                }
                .disabled(viewModel.isLoading)
                .accessibilityIdentifier("onboarding.suggestions.continue")

                if viewModel.shouldOfferRetryFromSuggestions {
                    Button("Try again") {
                        withAnimation(.easeInOut(duration: 0.3)) {
                            viewModel.retryPersonalization()
                        }
                    }
                    .font(.callout.weight(.medium))
                    .foregroundColor(.watercolorSlate.opacity(0.78))
                    .buttonStyle(OnboardingTextButtonStyle())
                    .accessibilityIdentifier("onboarding.suggestions.retry")
                } else if viewModel.isShowingDefaultConfirmation {
                    Button("Personalize instead") {
                        withAnimation(.easeInOut(duration: 0.3)) {
                            viewModel.retryPersonalization()
                        }
                    }
                    .font(.callout.weight(.medium))
                    .foregroundColor(.watercolorSlate.opacity(0.78))
                    .buttonStyle(OnboardingTextButtonStyle())
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
            .background(footerBackground)
        }
        .accessibilityIdentifier("onboarding.suggestions.screen")
    }

    // MARK: - Fast News

    private var fastNewsView: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    headerBlock(
                        eyebrow: "FAST NEWS",
                        title: "Add quick-hit sources",
                        subtitle:
                            "Pick aggregators and subreddits for high-frequency headlines. Skip any you don't want.",
                        isLeading: true
                    )

                    aggregatorSection

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
                Text("\(selectedFastNewsCount) selected")
                    .font(.caption.weight(.semibold))
                    .monospacedDigit()
                    .foregroundColor(.watercolorSlate.opacity(0.65))

                primaryButton(fastNewsPrimaryTitle) {
                    Task { await viewModel.completeOnboarding() }
                }
                .disabled(viewModel.isLoading)
                .accessibilityIdentifier("onboarding.complete")

                Button("Back") {
                    withAnimation(.easeInOut(duration: 0.3)) {
                        viewModel.returnToSuggestions()
                    }
                }
                .font(.callout.weight(.medium))
                .foregroundColor(.watercolorSlate.opacity(0.72))
                .buttonStyle(OnboardingTextButtonStyle())
                .accessibilityIdentifier("onboarding.fastnews.back")

                if let error = viewModel.errorMessage {
                    Text(error)
                        .font(.caption)
                        .foregroundColor(.red)
                }
            }
            .padding(.horizontal, 24)
            .padding(.top, 14)
            .padding(.bottom, 16)
            .background(footerBackground)
        }
        .accessibilityIdentifier("onboarding.fastnews.screen")
    }

    private var aggregatorSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "bolt.horizontal")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundColor(.watercolorSlate.opacity(0.55))
                Text("AGGREGATORS")
                    .font(.editorialMeta)
                    .foregroundColor(.watercolorSlate.opacity(0.55))
                    .tracking(1.5)

                Spacer()

                Text("\(viewModel.selectedAggregators.count)/\(onboardingAggregatorOptions.count)")
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
                ForEach(onboardingAggregatorOptions) { option in
                    aggregatorRow(option: option)
                }
            }
        }
    }

    private func aggregatorRow(option: OnboardingAggregatorOption) -> some View {
        let isSelected = viewModel.selectedAggregators.contains(option.key)
        let isBrutalist = option.key == "brutalist"
        return VStack(alignment: .leading, spacing: 10) {
            Button {
                viewModel.toggleAggregator(option)
            } label: {
                HStack(spacing: 12) {
                    ZStack {
                        Circle()
                            .fill(Color.watercolorSlate.opacity(isSelected ? 0.16 : 0.08))
                            .frame(width: 36, height: 36)
                        Image(systemName: option.icon)
                            .font(.system(size: 15, weight: .medium))
                            .foregroundColor(.watercolorSlate)
                    }

                    VStack(alignment: .leading, spacing: 2) {
                        Text(option.title)
                            .font(.callout.weight(.semibold))
                            .foregroundColor(.watercolorSlate)
                        Text(option.subtitle)
                            .font(.caption)
                            .foregroundColor(.watercolorSlate.opacity(0.62))
                            .lineLimit(2)
                    }

                    Spacer()

                    OnboardingSelectionDot(isSelected: isSelected)
                }
                .padding(12)
                .background(
                    RoundedRectangle(cornerRadius: 18)
                        .fill(Color.watercolorBase.opacity(isSelected ? 0.92 : 0.7))
                        .overlay(
                            RoundedRectangle(cornerRadius: 18)
                                .stroke(
                                    isSelected
                                        ? Color.watercolorPaleEmerald.opacity(0.4)
                                        : Color.watercolorSlate.opacity(0.10),
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
            reduceMotion ? .linear(duration: 0.01) : .easeInOut(duration: 0.2),
            value: isSelected
        )
    }

    private var brutalistTopicChips: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("TOPICS")
                .font(.editorialMeta)
                .tracking(1.4)
                .foregroundColor(.watercolorSlate.opacity(0.55))

            FlowLayout(spacing: 6) {
                ForEach(onboardingBrutalistTopics, id: \.self) { topic in
                    let isOn = viewModel.selectedBrutalistTopics.contains(topic)
                    Button {
                        viewModel.toggleBrutalistTopic(topic)
                    } label: {
                        Text(topic.capitalized)
                            .font(.caption.weight(.semibold))
                            .foregroundColor(
                                isOn
                                    ? Color.watercolorSlate.opacity(0.95)
                                    : Color.watercolorSlate.opacity(0.62)
                            )
                            .padding(.horizontal, 11)
                            .padding(.vertical, 6)
                            .background(
                                Capsule(style: .continuous)
                                    .fill(
                                        isOn
                                            ? Color.watercolorPaleEmerald.opacity(0.22)
                                            : Color.clear
                                    )
                                    .overlay(
                                        Capsule(style: .continuous)
                                            .strokeBorder(
                                                isOn
                                                    ? Color.watercolorPaleEmerald.opacity(0.4)
                                                    : Color.watercolorSlate.opacity(0.18),
                                                lineWidth: 0.75
                                            )
                                    )
                            )
                    }
                    .buttonStyle(OnboardingTextButtonStyle())
                    .accessibilityIdentifier(
                        "onboarding.fastnews.brutalist.topic.\(topic)"
                    )
                }
            }
        }
    }

    private var footerBackground: some View {
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
        .buttonStyle(OnboardingPrimaryPressStyle())
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

    private var isFinalizingLanes: Bool {
        !viewModel.discoveryLanes.isEmpty
            && completedLaneCount == viewModel.discoveryLanes.count
    }

    private var finalizingRow: some View {
        HStack(alignment: .center, spacing: 12) {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: [
                                Color.watercolorMistyBlue.opacity(0.22),
                                Color.watercolorPaleEmerald.opacity(0.22),
                            ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: 26, height: 26)

                FinalizingSparkle()
            }

            VStack(alignment: .leading, spacing: 1) {
                Text("Finalizing")
                    .font(.callout.weight(.medium))
                    .foregroundColor(.watercolorSlate.opacity(0.95))
                Text("Shaping your first picks")
                    .font(.caption)
                    .foregroundColor(.watercolorSlate.opacity(0.55))
            }

            Spacer()
        }
        .padding(.vertical, 4)
    }

    private var currentStepInfo: (number: Int, label: String) {
        switch viewModel.step {
        case .intro, .choice:
            return (1, "Choose your start")
        case .audio, .loading:
            return (2, viewModel.step == .audio ? "Voice setup" : "Matching sources")
        case .suggestions:
            return (3, "Review picks")
        case .fastNews:
            return (4, "Fast news")
        }
    }

    private var selectedLongformCount: Int {
        viewModel.selectedSourceKeys.count
    }

    private var selectedFastNewsCount: Int {
        viewModel.selectedAggregators.count + viewModel.selectedSubreddits.count
    }

    private var suggestionsPrimaryTitle: String {
        if !viewModel.isShowingDefaultConfirmation && selectedLongformCount > 0 {
            return "Continue with \(selectedLongformCount)"
        }
        return "Continue"
    }

    private var fastNewsPrimaryTitle: String {
        if selectedFastNewsCount == 0 {
            return "Start reading"
        }
        return "Start with \(selectedFastNewsCount + selectedLongformCount) sources"
    }

    private var loadingFootnote: String {
        if isFinalizingLanes {
            return "Almost there"
        }
        if !viewModel.discoveryLanes.isEmpty {
            return "\(completedLaneCount) of \(viewModel.discoveryLanes.count) sources ready"
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

private struct OnboardingPrimaryPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1.0)
            .animation(.spring(response: 0.28, dampingFraction: 0.82), value: configuration.isPressed)
    }
}

private struct OnboardingTextButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .frame(minHeight: 44)
            .contentShape(Rectangle())
            .scaleEffect(configuration.isPressed ? 0.96 : 1.0)
            .opacity(configuration.isPressed ? 0.72 : 1.0)
            .animation(.spring(response: 0.28, dampingFraction: 0.82), value: configuration.isPressed)
    }
}

struct OnboardingSelectionDot: View {
    let isSelected: Bool

    var body: some View {
        ZStack {
            Circle()
                .fill(
                    isSelected
                        ? Color.watercolorPaleEmerald.opacity(0.22)
                        : Color.clear
                )
                .overlay(
                    Circle()
                        .strokeBorder(
                            isSelected
                                ? Color.watercolorPaleEmerald.opacity(0.55)
                                : Color.watercolorSlate.opacity(0.28),
                            lineWidth: isSelected ? 1.2 : 1
                        )
                )
                .frame(width: 26, height: 26)

            if isSelected {
                Image(systemName: "checkmark")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(.watercolorPaleEmerald)
                    .transition(.scale(scale: 0.4).combined(with: .opacity))
            }
        }
        .animation(.spring(response: 0.28, dampingFraction: 0.85), value: isSelected)
    }
}

private struct FinalizingSparkle: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var breathing = false

    var body: some View {
        Image(systemName: "sparkles")
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(
                LinearGradient(
                    colors: [.watercolorMistyBlue, .watercolorPaleEmerald],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .scaleEffect(breathing ? 1.12 : 0.92)
            .opacity(breathing ? 1.0 : 0.78)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 1.3).repeatForever(autoreverses: true)) {
                    breathing = true
                }
            }
    }
}
