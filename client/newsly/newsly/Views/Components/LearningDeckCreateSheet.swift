//
//  LearningDeckCreateSheet.swift
//  newsly
//

import SwiftUI

struct LearningDeckCreateSheet: View {
    let sourceTitle: String?
    let requiresURL: Bool
    let isSubmitting: Bool
    let onCreate: (_ url: String?, _ interestsPrompt: String?) async -> Bool

    @Environment(\.dismiss) private var dismiss
    @State private var focusRecorder = RootDependencyFactory.makeLearningDeckFocusRecorder()
    @State private var urlText = ""
    @State private var interestsText = ""

    private var canSubmit: Bool {
        if isSubmitting {
            return false
        }
        if requiresURL {
            guard let normalizedURLText else { return false }
            return URL(string: normalizedURLText) != nil
        }
        return true
    }

    private var normalizedURLText: String? {
        nonEmptyTrimmed(urlText)
    }

    private var normalizedInterestsText: String? {
        nonEmptyTrimmed(interestsText)
    }

    private var normalizedSourceTitle: String? {
        guard let sourceTitle else { return nil }
        return nonEmptyTrimmed(sourceTitle)
    }

    private let focusSuggestions = ["Key takeaways", "How it works", "Why it matters"]

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Learning Deck")
                        .font(.terracottaHeadlineMedium)
                        .foregroundStyle(Color.onSurface)

                    if let sourceTitle = normalizedSourceTitle {
                        Text(sourceTitle)
                            .font(.terracottaBodyMedium)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16)
                .learningDeckCreatePanelSurface()

                if requiresURL {
                    TextField("Article, GitHub, podcast, or PDF URL", text: $urlText)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.terracottaBodyLarge)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                        .learningDeckCreateInputSurface()
                        .accessibilityLabel("Learning Deck URL")
                        .accessibilityIdentifier("learning_deck.create.url")
                }

                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 10) {
                        HStack(spacing: 6) {
                            Text("Focus")
                                .font(.terracottaBodyMedium.weight(.semibold))
                                .foregroundStyle(Color.onSurface)
                            Text("optional")
                                .font(.terracottaBodySmall)
                                .foregroundStyle(Color.onSurfaceSecondary)
                        }

                        Spacer(minLength: 0)

                        TapToTalkMicButton(
                            isEnabled: !isSubmitting && !focusRecorder.isTranscribing,
                            isRecording: focusRecorder.isRecording,
                            isTranscribing: focusRecorder.isTranscribing,
                            isBusy: focusRecorder.isVoiceActionInFlight && !focusRecorder.isRecording,
                            size: 48,
                            action: {
                                Task {
                                    await focusRecorder.toggleRecording { transcript in
                                        appendInterestsTranscript(transcript)
                                    }
                                }
                            }
                        )
                        .accessibilityLabel(focusRecorder.isRecording ? "Stop recording focus" : "Record deck focus")
                        .accessibilityHint(
                            focusRecorder.isRecording
                                ? "Tap to stop and add the transcript to the Focus field"
                                : "Tap to dictate what this Learning Deck should focus on"
                        )
                        .accessibilityIdentifier("learning_deck.focus_mic")
                    }

                    ZStack(alignment: .topLeading) {
                        if normalizedInterestsText == nil {
                            Text("What should this deck zoom in on? — e.g. the security tradeoffs, or just the key takeaways")
                                .font(.terracottaBodyMedium)
                                .foregroundStyle(Color.onSurfaceSecondary.opacity(0.55))
                                .padding(.horizontal, 15)
                                .padding(.vertical, 18)
                                .allowsHitTesting(false)
                        }

                        TextEditor(text: $interestsText)
                            .font(.terracottaBodyMedium)
                            .scrollContentBackground(.hidden)
                            .frame(minHeight: 126)
                            .padding(10)
                    }
                    .learningDeckCreateInputSurface()
                    .accessibilityIdentifier("learning_deck.create.focus")

                    if normalizedInterestsText == nil {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 8) {
                                ForEach(focusSuggestions, id: \.self) { suggestion in
                                    Button {
                                        appendInterestsTranscript(suggestion)
                                    } label: {
                                        Text(suggestion)
                                            .font(.terracottaBodySmall.weight(.semibold))
                                            .foregroundStyle(Color.onSurface)
                                            .padding(.horizontal, 12)
                                            .padding(.vertical, 7)
                                            .background(Color.surfaceSecondary, in: Capsule())
                                            .overlay {
                                                Capsule()
                                                    .stroke(Color.outlineVariant.opacity(0.18), lineWidth: 1)
                                            }
                                            .contentShape(Capsule())
                                    }
                                    .buttonStyle(.plain)
                                    .accessibilityIdentifier("learning_deck.create.focus_suggestion")
                                }
                            }
                        }
                    }

                    focusRecordingStatus
                }
                .padding(16)
                .learningDeckCreatePanelSurface()

                Spacer(minLength: 0)
            }
            .padding(20)
            .disabled(isSubmitting)
            .background {
                LinearGradient(
                    colors: [
                        Color.surfacePrimary,
                        Color.surfaceContainer.opacity(0.42),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
                .ignoresSafeArea()
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                    .accessibilityIdentifier("learning_deck.create.cancel")
                }

                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task {
                            let didCreate = await onCreate(
                                requiresURL ? normalizedURLText : nil,
                                normalizedInterestsText
                            )
                            if didCreate {
                                dismiss()
                            }
                        }
                    } label: {
                        if isSubmitting {
                            ProgressView()
                        } else {
                            Text("Create")
                        }
                    }
                    .disabled(!canSubmit)
                    .accessibilityIdentifier("learning_deck.create.submit")
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .accessibilityIdentifier("learning_deck.create.sheet")
        .onDisappear {
            focusRecorder.cancelRecording()
        }
    }

    @ViewBuilder
    private var focusRecordingStatus: some View {
        if focusRecorder.isRecording {
            HStack(spacing: 6) {
                Circle()
                    .fill(Color.statusDestructive)
                    .frame(width: 7, height: 7)

                Text("Recording focus...")
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .accessibilityIdentifier("learning_deck.focus_recording")
        } else if focusRecorder.isTranscribing {
            HStack(spacing: 6) {
                ProgressView()
                    .controlSize(.small)

                Text("Transcribing focus...")
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .accessibilityIdentifier("learning_deck.focus_transcribing")
        } else if let errorMessage = focusRecorder.errorMessage {
            Text(errorMessage)
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.statusDestructive)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("learning_deck.focus_recording_error")
        }
    }

    private func appendInterestsTranscript(_ transcript: String) {
        guard let normalizedTranscript = nonEmptyTrimmed(transcript) else { return }
        guard let existingText = nonEmptyTrimmed(interestsText) else {
            interestsText = normalizedTranscript
            return
        }
        interestsText = "\(existingText)\n\(normalizedTranscript)"
    }

}

private extension View {
    func learningDeckCreatePanelSurface() -> some View {
        self
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.22), lineWidth: 1)
            }
    }

    @ViewBuilder
    func learningDeckCreateInputSurface() -> some View {
        glassSurface(
            in: RoundedRectangle(cornerRadius: 14, style: .continuous),
            tint: Color.surfaceSecondary,
            opacity: 0.22,
            interactive: true,
            fallback: .tintStroke(fillOpacity: 1, strokeOpacity: 0.18)
        )
    }
}
