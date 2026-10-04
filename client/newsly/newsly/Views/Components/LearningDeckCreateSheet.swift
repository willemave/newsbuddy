//
//  LearningDeckCreateSheet.swift
//  newsly
//

import SwiftUI

struct LearningDeckCreateSheet: View {
    let sourceTitle: String?
    let isSubmitting: Bool
    let onCreate: (_ interestsPrompt: String?) async -> Bool

    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var focusRecorder: LearningDeckFocusRecorder
    @State private var interestsText = ""

    private var canSubmit: Bool {
        !isSubmitting
    }

    private var normalizedInterestsText: String? {
        nonEmptyTrimmed(interestsText)
    }

    private var normalizedSourceTitle: String? {
        guard let sourceTitle else { return nil }
        return nonEmptyTrimmed(sourceTitle)
    }

    init(
        sourceTitle: String?,
        isSubmitting: Bool,
        focusRecorder: LearningDeckFocusRecorder,
        onCreate: @escaping (_ interestsPrompt: String?) async -> Bool
    ) {
        self.sourceTitle = sourceTitle
        self.isSubmitting = isSubmitting
        self._focusRecorder = State(initialValue: focusRecorder)
        self.onCreate = onCreate
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let sourceTitle = normalizedSourceTitle {
                        Text(sourceTitle)
                            .font(.modalBody)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 10) {
                            HStack(spacing: 6) {
                                Text("Focus")
                                    .font(.modalBody.weight(.semibold))
                                    .foregroundStyle(Color.onSurface)
                                Text("optional")
                                    .font(.modalSupporting)
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
                            .accessibilityValue(focusRecorder.state.accessibilityValue)
                        }

                        TextField(
                            "Deck focus",
                            text: $interestsText,
                            prompt: Text("What should this deck focus on?")
                                .foregroundStyle(Color.onSurfaceTertiary),
                            axis: .vertical
                        )
                        .font(.modalBody)
                        .foregroundStyle(Color.onSurface)
                        .lineLimit(3...6)
                        .padding(14)
                        .background(Color.surfaceSecondary, in: RoundedRectangle(cornerRadius: 14))
                        .accessibilityLabel("Deck focus")
                        .accessibilityIdentifier("learning_deck.create.focus")

                        focusRecordingStatus
                    }
                    .padding(16)
                    .learningDeckCreatePanelSurface()
                }
                .padding(16)
            }
            .scrollDismissesKeyboard(.interactively)
            .disabled(isSubmitting)
            .navigationTitle("Learning Deck")
            .navigationBarTitleDisplayMode(.inline)
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
                    .font(.modalBody)
                    .accessibilityIdentifier("learning_deck.create.cancel")
                }

                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task {
                            let didCreate = await onCreate(normalizedInterestsText)
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
                    .font(.modalBody.weight(.semibold))
                    .disabled(!canSubmit)
                    .accessibilityIdentifier("learning_deck.create.submit")
                }
            }
        }
        .presentationDetents(dynamicTypeSize.isAccessibilitySize ? [.large] : [.medium, .large])
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
                    .font(.modalSupporting)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .accessibilityIdentifier("learning_deck.focus_recording")
        } else if focusRecorder.isTranscribing {
            HStack(spacing: 6) {
                ProgressView()
                    .controlSize(.small)

                Text("Transcribing focus...")
                    .font(.modalSupporting)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .accessibilityIdentifier("learning_deck.focus_transcribing")
        } else if let errorMessage = focusRecorder.errorMessage {
            Text(errorMessage)
                .font(.modalSupporting)
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
}
