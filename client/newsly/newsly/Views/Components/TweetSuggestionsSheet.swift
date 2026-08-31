//
//  TweetSuggestionsSheet.swift
//  newsly
//
//  Sheet for generating and sharing tweet suggestions.
//

import SwiftUI

struct TweetSuggestionsSheet: View {
    let contentId: Int
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var viewModel: TweetSuggestionsViewModel

    init(contentId: Int, viewModel: TweetSuggestionsViewModel) {
        self.contentId = contentId
        self._viewModel = State(initialValue: viewModel)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    // Creativity Slider + Voice Input (combined)
                    controlsSection

                    // Suggestions Cards
                    if viewModel.isLoading {
                        loadingView
                    } else if let error = viewModel.errorMessage {
                        errorView(message: error)
                    } else {
                        suggestionsSection
                    }

                    // Regenerate Button
                    regenerateButton
                }
                .padding()
            }
            .navigationTitle("Tweet Suggestions")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") {
                        dismiss()
                    }
                }

                // Model selector (trailing)
                ToolbarItem(placement: .navigationBarTrailing) {
                    Menu {
                        Section {
                            Text("Current: \(viewModel.selectedProvider.displayName)")
                                .font(.appCaption)
                        }
                        Section("Switch Model") {
                            ForEach(ChatModelProvider.tweetProviders, id: \.self) { provider in
                                Button {
                                    Task {
                                        await viewModel.switchProvider(to: provider)
                                    }
                                } label: {
                                    Label(provider.displayName, systemImage: provider.iconName)
                                }
                                .disabled(provider == viewModel.selectedProvider)
                            }
                        }
                    } label: {
                        HStack(spacing: 4) {
                            Text(viewModel.selectedProvider.displayName)
                            Image(systemName: "chevron.down")
                                .font(.appCaption2)
                        }
                        .font(.appSubheadline)
                        .foregroundColor(Color.onSurface)
                    }
                    .disabled(viewModel.isLoading || viewModel.isRegenerating)
                }
            }
            .task {
                await viewModel.initialize(contentId: contentId)
            }
        }
        .accessibilityIdentifier("content.tweet.sheet")
        .onDisappear {
            viewModel.cancelVoiceRecording()
        }
    }

    // MARK: - Controls Section (Creativity + Voice)

    private var controlsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Header row: Creativity label + badge
            HStack {
                Text("Creativity")
                    .font(.appHeadline)

                Spacer()

                Text(viewModel.creativityLabel)
                    .font(.appSubheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 4)
                    .background(creativityColor.opacity(0.2))
                    .cornerRadius(8)
            }

            // Slider row with voice button
            HStack(spacing: 16) {
                Text("1")
                    .font(.appCaption)
                    .foregroundColor(Color.onSurfaceSecondary)
                Slider(
                    value: Binding(
                        get: { Double(viewModel.creativity) },
                        set: { newValue in
                            let intValue = Int(newValue)
                            viewModel.creativity = intValue
                            viewModel.creativityChanged(to: intValue)
                        }
                    ),
                    in: 1...10,
                    step: 1
                )
                .tint(creativityColor)
                .disabled(viewModel.isLoading || viewModel.isRegenerating)
                Text("10")
                    .font(.appCaption)
                    .foregroundColor(Color.onSurfaceSecondary)

                if viewModel.voiceDictationAvailable {
                    voiceButton
                }
            }

            // Transcribing status (only shown when active)
            if viewModel.isTranscribing {
                HStack(spacing: 4) {
                    ProgressView()
                        .scaleEffect(0.7)
                    Text("Transcribing...")
                        .font(.appCaption)
                        .foregroundColor(Color.onSurfaceSecondary)
                }
            }
        }
        .padding()
        .background(Color.surfaceSecondary)
        .cornerRadius(12)
    }

    private var creativityColor: Color {
        // Single accent for the creativity meter.
        .brandPrimary
    }

    private var voiceButton: some View {
        Button {
            Task {
                if viewModel.isRecording {
                    await viewModel.stopVoiceRecording()
                } else {
                    await viewModel.startVoiceRecording()
                }
            }
        } label: {
            if viewModel.voiceState == .starting {
                ProgressView()
                    .frame(width: 40, height: 40)
            } else {
                Image(systemName: viewModel.isRecording ? "stop.circle.fill" : "mic.circle.fill")
                    .font(.appSymbol(size: 40))
                    .foregroundColor(viewModel.isRecording ? .statusDestructive : .brandPrimary)
                    .symbolEffect(.pulse, isActive: viewModel.isRecording && !reduceMotion)
            }
        }
        .disabled(
            viewModel.voiceState == .starting
                || viewModel.isTranscribing
                || viewModel.isLoading
                || viewModel.isRegenerating
        )
        .accessibilityLabel(viewModel.isRecording ? "Stop tweet adjustment recording" : "Record tweet adjustment")
        .accessibilityIdentifier("content.tweet.voice_mic")
        .accessibilityValue(viewModel.voiceState.accessibilityValue)
    }

    // MARK: - Suggestions Section

    private var suggestionsSection: some View {
        VStack(spacing: 16) {
            ForEach(viewModel.suggestions) { suggestion in
                TweetSuggestionCard(
                    suggestion: suggestion,
                    isSelected: viewModel.selectedSuggestionId == suggestion.id,
                    onSelect: { viewModel.selectSuggestion(suggestion) },
                    onShare: { viewModel.shareToTwitter(suggestion: suggestion) },
                    onCopy: { viewModel.copyToClipboard(suggestion: suggestion) }
                )
            }
        }
    }

    // MARK: - Loading View

    private var loadingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.5)
            Text("Generating tweet suggestions...")
                .font(.appSubheadline)
                .foregroundColor(Color.onSurfaceSecondary)
        }
        .frame(minHeight: 200)
    }

    // MARK: - Error View

    private func errorView(message: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle")
                .font(.appLargeTitle)
                .foregroundColor(.brandPrimary)
            Text(message)
                .font(.appSubheadline)
                .foregroundColor(Color.onSurfaceSecondary)
                .multilineTextAlignment(.center)
            Button(viewModel.hasVoiceError ? "Try Voice Again" : "Try Again") {
                Task {
                    if viewModel.hasVoiceError {
                        await viewModel.retryVoiceRecording()
                    } else {
                        await viewModel.generateSuggestions()
                    }
                }
            }
            .buttonStyle(.borderedProminent)
            .accessibilityIdentifier(
                viewModel.hasVoiceError
                    ? "content.tweet.voice_retry"
                    : "content.tweet.generate_retry"
            )
        }
        .frame(minHeight: 200)
    }

    // MARK: - Regenerate Button

    private var regenerateButton: some View {
        Button {
            Task {
                await viewModel.regenerate()
            }
        } label: {
            HStack {
                if viewModel.isRegenerating {
                    ProgressView()
                        .scaleEffect(0.8)
                        .tint(.white)
                } else {
                    Image(systemName: "arrow.clockwise")
                }
                Text("Regenerate")
            }
            .frame(maxWidth: .infinity)
            .padding()
        }
        .buttonStyle(.borderedProminent)
        .tint(Color.brandPrimary)
        .disabled(viewModel.isLoading || viewModel.isRegenerating)
    }
}

// MARK: - Tweet Suggestion Card

struct TweetSuggestionCard: View {
    let suggestion: TweetSuggestion
    let isSelected: Bool
    let onSelect: () -> Void
    let onShare: () -> Void
    let onCopy: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Style Label Badge
            if let styleLabel = suggestion.styleLabel, !styleLabel.isEmpty {
                Text(styleLabel.capitalized)
                    .font(.appCaption)
                    .fontWeight(.medium)
                    .foregroundColor(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(styleColor(for: styleLabel))
                    .cornerRadius(12)
            }

            // Tweet Text
            Text(suggestion.text)
                .font(.appBody)
                .foregroundColor(Color.onSurface)
                .fixedSize(horizontal: false, vertical: true)

            // Character Count
            HStack {
                Text("\(suggestion.text.count)/280")
                    .font(.appCaption)
                    .foregroundColor(suggestion.text.count > 280 ? .statusDestructive : Color.onSurfaceSecondary)

                Spacer()

                // Action Buttons
                HStack(spacing: 16) {
                    Button {
                        onCopy()
                    } label: {
                        Image(systemName: "doc.on.doc")
                            .font(.appBody)
                    }
                    .foregroundColor(Color.onSurfaceSecondary)

                    Button {
                        onShare()
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "paperplane.fill")
                            Text("Tweet")
                        }
                        .font(.appSubheadline)
                        .fontWeight(.medium)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Color.brandPrimary)
                }
            }
        }
        .padding()
        .background(
            RoundedRectangle(cornerRadius: 16)
                .fill(Color.surfaceSecondary)
                .appShadow(.subtle)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(isSelected ? Color.brandPrimary : Color.clear, lineWidth: 2)
        )
        .onTapGesture {
            onSelect()
        }
    }

    private func styleColor(for _: String) -> Color {
        // Style labels are neutral metadata, not per-style hues.
        .onSurfaceSecondary
    }
}

#Preview {
    EmptyView()
}
