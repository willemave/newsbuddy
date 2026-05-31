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
    @StateObject private var viewModel = TweetSuggestionsViewModel()

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
                                .font(.caption)
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
                                .font(.caption2)
                        }
                        .font(.subheadline)
                        .foregroundColor(Color.onSurface)
                    }
                    .disabled(viewModel.isLoading || viewModel.isRegenerating)
                }
            }
            .task {
                await viewModel.initialize(contentId: contentId)
            }
        }
    }

    // MARK: - Controls Section (Creativity + Voice)

    private var controlsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Header row: Creativity label + badge
            HStack {
                Text("Creativity")
                    .font(.headline)

                Spacer()

                Text(viewModel.creativityLabel)
                    .font(.subheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 4)
                    .background(creativityColor.opacity(0.2))
                    .cornerRadius(8)
            }

            // Slider row with voice button
            HStack(spacing: 16) {
                Text("1")
                    .font(.caption)
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
                    .font(.caption)
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
                        .font(.caption)
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
            Image(systemName: viewModel.isRecording ? "stop.circle.fill" : "mic.circle.fill")
                .font(.system(size: 40))
                .foregroundColor(viewModel.isRecording ? .statusDestructive : .brandPrimary)
                .symbolEffect(.pulse, isActive: viewModel.isRecording)
        }
        .disabled(viewModel.isTranscribing || viewModel.isLoading || viewModel.isRegenerating)
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
                .font(.subheadline)
                .foregroundColor(Color.onSurfaceSecondary)
        }
        .frame(minHeight: 200)
    }

    // MARK: - Error View

    private func errorView(message: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle")
                .font(.largeTitle)
                .foregroundColor(.brandPrimary)
            Text(message)
                .font(.subheadline)
                .foregroundColor(Color.onSurfaceSecondary)
                .multilineTextAlignment(.center)
            Button("Retry") {
                Task {
                    await viewModel.generateSuggestions()
                }
            }
            .buttonStyle(.borderedProminent)
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
                    .font(.caption)
                    .fontWeight(.medium)
                    .foregroundColor(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(styleColor(for: styleLabel))
                    .cornerRadius(12)
            }

            // Tweet Text
            Text(suggestion.text)
                .font(.body)
                .foregroundColor(Color.onSurface)
                .fixedSize(horizontal: false, vertical: true)

            // Character Count
            HStack {
                Text("\(suggestion.text.count)/280")
                    .font(.caption)
                    .foregroundColor(suggestion.text.count > 280 ? .statusDestructive : Color.onSurfaceSecondary)

                Spacer()

                // Action Buttons
                HStack(spacing: 16) {
                    Button {
                        onCopy()
                    } label: {
                        Image(systemName: "doc.on.doc")
                            .font(.body)
                    }
                    .foregroundColor(Color.onSurfaceSecondary)

                    Button {
                        onShare()
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "paperplane.fill")
                            Text("Tweet")
                        }
                        .font(.subheadline)
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
                .shadow(color: .black.opacity(0.1), radius: 4, x: 0, y: 2)
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
    TweetSuggestionsSheet(contentId: 1)
}
