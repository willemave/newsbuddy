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
        case .fastNews, .aggregators:
            aggregatorsView
                .transition(screenTransition)
        case .reddit:
            redditView
                .transition(screenTransition)
        }
    }

    private var progressHeader: some View {
        HStack(spacing: 6) {
            ForEach(0..<progressStepTotal, id: \.self) { index in
                Capsule()
                    .fill(
                        index < currentStepInfo.number
                            ? Color.onboardingText.opacity(0.55)
                            : Color.onboardingText.opacity(0.14)
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

    private var progressStepTotal: Int { 5 }

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
                        .foregroundColor(.onboardingText.opacity(0.55))
                    Text("Newsbuddy")
                        .font(.watercolorDisplay)
                        .foregroundColor(.onboardingText)
                        .multilineTextAlignment(.center)
                    Text("I'm going to help you get onboarded.\nLet's get going.")
                        .font(.watercolorSubtitle)
                        .foregroundColor(.onboardingText.opacity(0.74))
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
                            .font(.appBody.weight(.medium))
                        Text("Personalize with voice")
                            .font(.appCallout.weight(.semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .foregroundColor(.onboardingSurface)
                    .background(primaryButtonBackground)
                }
                .buttonStyle(OnboardingPrimaryPressStyle())
                .accessibilityIdentifier("onboarding.choice.personalized")

                Button {
                    viewModel.chooseDefaults()
                } label: {
                    Text("Skip personalization")
                        .font(.appCallout.weight(.medium))
                        .foregroundColor(.onboardingText.opacity(0.72))
                }
                .buttonStyle(OnboardingTextButtonStyle())
                .accessibilityIdentifier("onboarding.choice.skip")
            }
            .padding(12)
            .background(cardSurface(cornerRadius: 36))

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.appCaption)
                    .foregroundColor(.statusDestructive)
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
                .font(.appCallout.weight(.medium))
                .foregroundColor(.onboardingText.opacity(0.72))
                .buttonStyle(OnboardingTextButtonStyle())
                .padding(.bottom, 8)
                .accessibilityIdentifier("onboarding.audio.skip")
            }

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
        .accessibilityIdentifier("onboarding.audio.screen")
    }

    private var audioProcessingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
                .tint(.onboardingText)
            Text("Processing your interests...")
                .font(.appCallout)
                .foregroundColor(.onboardingText.opacity(0.7))

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
                        .tint(.onboardingText)
                    Text("Preparing search...")
                        .font(.appCallout)
                        .foregroundColor(.onboardingText.opacity(0.7))
                } else {
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
                        reduceMotion ? .linear(duration: 0.01) : .easeInOut(duration: 0.3),
                        value: isFinalizingLanes
                    )
                }
            }

            Spacer()

            VStack(spacing: 14) {
                if let loadingFootnote {
                    Text(loadingFootnote)
                        .font(.appCaption)
                        .foregroundColor(.onboardingText.opacity(0.62))
                }

                if let message = viewModel.discoveryErrorMessage {
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
                            .fill(Color.onboardingAmbientTertiary.opacity(0.18))
                            .overlay(
                                RoundedRectangle(cornerRadius: 16, style: .continuous)
                                    .stroke(Color.onboardingAmbientTertiary.opacity(0.32), lineWidth: 0.5)
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
                        withAnimation(.easeInOut(duration: 0.3)) {
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
                        .font(.appCaption.weight(.semibold))
                        .monospacedDigit()
                        .foregroundColor(.onboardingText.opacity(0.65))
                }

                primaryButton("Continue") {
                    withAnimation(.easeInOut(duration: 0.3)) {
                        viewModel.advanceToAggregators()
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
                    .font(.appCallout.weight(.medium))
                    .foregroundColor(.onboardingText.opacity(0.78))
                    .buttonStyle(OnboardingTextButtonStyle())
                    .accessibilityIdentifier("onboarding.suggestions.retry")
                } else if viewModel.isShowingDefaultConfirmation {
                    Button("Personalize instead") {
                        withAnimation(.easeInOut(duration: 0.3)) {
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
            .padding(.horizontal, 24)
            .padding(.top, 14)
            .padding(.bottom, 16)
            .background(footerBackground)
        }
        .accessibilityIdentifier("onboarding.suggestions.screen")
    }

    // MARK: - Fast News

    private var aggregatorsView: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    headerBlock(
                        eyebrow: "FAST NEWS",
                        title: "Add news aggregators",
                        subtitle: "Broad headline streams across tech, science, finance, politics, and media.",
                        isLeading: true
                    )

                    aggregatorSection
                }
                .padding(.horizontal, 24)
                .padding(.top, 16)
                .padding(.bottom, 128)
            }

            VStack(spacing: 10) {
                Text("\(viewModel.selectedAggregators.count) selected")
                    .font(.appCaption.weight(.semibold))
                    .monospacedDigit()
                    .foregroundColor(.onboardingText.opacity(0.65))

                primaryButton("Continue") {
                    withAnimation(.easeInOut(duration: 0.3)) {
                        viewModel.advanceToReddit()
                    }
                }
                .disabled(viewModel.isLoading)
                .accessibilityIdentifier("onboarding.aggregators.continue")

                Button("Back") {
                    withAnimation(.easeInOut(duration: 0.3)) {
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
            .padding(.horizontal, 24)
            .padding(.top, 14)
            .padding(.bottom, 16)
            .background(footerBackground)
        }
        .accessibilityIdentifier("onboarding.aggregators.screen")
    }

    private var redditView: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    headerBlock(
                        eyebrow: "REDDIT",
                        title: "Add subreddit feeds",
                        subtitle: "Focused communities add topic-level posts alongside the broader headline mix.",
                        isLeading: true
                    )

                    if viewModel.subredditSuggestions.isEmpty {
                        Text("No Reddit matches found. You can start without subreddit feeds.")
                            .font(.appCallout)
                            .foregroundColor(.onboardingText.opacity(0.7))
                            .padding(.vertical, 20)
                    } else {
                        suggestionSection(
                            title: "SUBREDDITS",
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
                Text("\(viewModel.selectedSubreddits.count) selected")
                    .font(.appCaption.weight(.semibold))
                    .monospacedDigit()
                    .foregroundColor(.onboardingText.opacity(0.65))

                primaryButton(completionPrimaryTitle) {
                    Task { await viewModel.completeOnboarding() }
                }
                .disabled(viewModel.isLoading)
                .accessibilityIdentifier("onboarding.complete")

                Button("Back") {
                    withAnimation(.easeInOut(duration: 0.3)) {
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
            .padding(.horizontal, 24)
            .padding(.top, 14)
            .padding(.bottom, 16)
            .background(footerBackground)
        }
        .accessibilityIdentifier("onboarding.reddit.screen")
    }

    private var aggregatorSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "bolt.horizontal")
                    .font(.appSymbol(size: 9, weight: .semibold))
                    .foregroundColor(.onboardingText.opacity(0.55))
                Text("AGGREGATORS")
                    .font(.editorialMeta)
                    .foregroundColor(.onboardingText.opacity(0.55))
                    .tracking(1.5)

                Spacer()

                Text("\(viewModel.selectedAggregators.count)/\(onboardingAggregatorOptions.count)")
                    .font(.appCaption.weight(.semibold))
                    .monospacedDigit()
                    .foregroundColor(.onboardingText.opacity(0.68))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Capsule().fill(Color.onboardingText.opacity(0.08)))
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
            reduceMotion ? .linear(duration: 0.01) : .easeInOut(duration: 0.2),
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
                    let isOn = viewModel.selectedBrutalistTopics.contains(topic)
                    Button {
                        viewModel.toggleBrutalistTopic(topic)
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
                colors: [.clear, Color.onboardingSurface.opacity(0.28)],
                startPoint: .top,
                endPoint: .bottom
            )

            Rectangle()
                .fill(Color.onboardingText.opacity(0.08))
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
                    .font(.appSymbol(size: 9, weight: .semibold))
                    .foregroundColor(.onboardingText.opacity(0.55))
                Text(title)
                    .font(.editorialMeta)
                    .foregroundColor(.onboardingText.opacity(0.55))
                    .tracking(1.5)

                Spacer()

                Text("\(items.count)")
                    .font(.appCaption.weight(.semibold))
                    .monospacedDigit()
                    .foregroundColor(.onboardingText.opacity(0.68))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Capsule().fill(Color.onboardingText.opacity(0.08)))
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
                .font(.appCallout.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .foregroundColor(.onboardingSurface)
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
                .foregroundColor(.onboardingText.opacity(0.58))

            Text(title)
                .font(.appTitle2)
                .foregroundColor(.onboardingText)
                .multilineTextAlignment(textAlignment)

            Text(subtitle)
                .font(.appCallout)
                .foregroundColor(.onboardingText.opacity(0.72))
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
                .foregroundColor(.onboardingText.opacity(0.58))

            Text(title)
                .font(.appCallout.weight(.semibold))
                .foregroundColor(.onboardingText)
                .fixedSize(horizontal: false, vertical: true)

            if !viewModel.inferredTopics.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Array(viewModel.inferredTopics.prefix(6)), id: \.self) { topic in
                            Text(topic)
                                .font(.appCaption.weight(.semibold))
                                .foregroundColor(.onboardingText)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 8)
                                .background(Capsule().fill(Color.onboardingText.opacity(0.08)))
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
            .fill(Color.onboardingSurface.opacity(0.76))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius)
                    .stroke(Color.onboardingText.opacity(0.10), lineWidth: 0.5)
            )
            .shadow(color: .black.opacity(0.05), radius: 16, x: 0, y: 10)
    }

    private var primaryButtonBackground: some View {
        RoundedRectangle(cornerRadius: 24)
            .fill(Color.onboardingText)
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
                                Color.onboardingAmbientPrimary.opacity(0.22),
                                Color.onboardingSelectionAccent.opacity(0.22),
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

    private var currentStepInfo: (number: Int, label: String) {
        switch viewModel.step {
        case .intro, .choice:
            return (1, "Choose your start")
        case .audio, .loading:
            return (2, viewModel.step == .audio ? "Voice setup" : "Matching sources")
        case .suggestions:
            return (3, "Review picks")
        case .fastNews, .aggregators:
            return (4, "News aggregators")
        case .reddit:
            return (5, "Reddit")
        }
    }

    private var selectedLongformCount: Int {
        viewModel.selectedSourceKeys.count
    }

    private var selectedShortformCount: Int {
        viewModel.selectedAggregators.count + viewModel.selectedSubreddits.count
    }

    private var completionPrimaryTitle: String {
        if selectedShortformCount == 0 {
            return "Start reading"
        }
        return "Start with \(selectedShortformCount + selectedLongformCount) sources"
    }

    private var loadingFootnote: String? {
        if viewModel.discoveryLanes.isEmpty {
            return "Usually takes about a minute or two"
        }
        return nil
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
                        ? Color.onboardingSelectionAccent.opacity(0.22)
                        : Color.clear
                )
                .overlay(
                    Circle()
                        .strokeBorder(
                            isSelected
                                ? Color.onboardingSelectionAccent.opacity(0.55)
                                : Color.onboardingText.opacity(0.28),
                            lineWidth: isSelected ? 1.2 : 1
                        )
                )
                .frame(width: 26, height: 26)

            if isSelected {
                Image(systemName: "checkmark")
                    .font(.appSymbol(size: 11, weight: .bold))
                    .foregroundColor(.onboardingSelectionAccent)
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
            .font(.appSymbol(size: 12, weight: .semibold))
            .foregroundStyle(
                LinearGradient(
                    colors: [.onboardingAmbientPrimary, .onboardingSelectionAccent],
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
