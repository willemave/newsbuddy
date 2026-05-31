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
    @StateObject private var focusRecorder = LearningDeckFocusRecorder()
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

                if requiresURL {
                    TextField("Article, GitHub, podcast, or PDF URL", text: $urlText)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.terracottaBodyLarge)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .background(Color.surfaceSecondary)
                        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                }

                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 10) {
                        Text("Focus")
                            .font(.terracottaBodyMedium.weight(.semibold))
                            .foregroundStyle(Color.onSurface)

                        Spacer(minLength: 0)

                        TapToTalkMicButton(
                            isEnabled: !isSubmitting && !focusRecorder.isTranscribing,
                            isRecording: focusRecorder.isRecording,
                            isBusy: focusRecorder.isVoiceActionInFlight && !focusRecorder.isRecording,
                            size: 34,
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

                    TextEditor(text: $interestsText)
                        .font(.terracottaBodyMedium)
                        .scrollContentBackground(.hidden)
                        .frame(minHeight: 126)
                        .padding(10)
                        .background(Color.surfaceSecondary)
                        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

                    focusRecordingStatus
                }

                Spacer(minLength: 0)
            }
            .padding(20)
            .background(Color.surfacePrimary.ignoresSafeArea())
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
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
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
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

    private func nonEmptyTrimmed(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

@MainActor
private final class LearningDeckFocusRecorder: ObservableObject {
    @Published private(set) var isRecording = false
    @Published private(set) var isTranscribing = false
    @Published private(set) var isVoiceActionInFlight = false
    @Published var errorMessage: String?

    private let transcriptionService: any SpeechTranscribing
    private var voiceDictationAvailable = false
    private var pendingTranscript: String?
    private var transcriptHandler: ((String) -> Void)?
    private var hasConfiguredCallbacks = false

    init(transcriptionService: (any SpeechTranscribing)? = nil) {
        self.transcriptionService = transcriptionService
            ?? SpeechTranscriberFactory.makeVoiceDictationTranscriber()
    }

    func toggleRecording(onTranscript: @escaping (String) -> Void) async {
        guard !isVoiceActionInFlight, !isTranscribing else { return }
        transcriptHandler = onTranscript

        if isRecording {
            await stopRecording()
        } else {
            await startRecording()
        }
    }

    func cancelRecording() {
        guard hasConfiguredCallbacks || isRecording || isTranscribing || pendingTranscript != nil else {
            return
        }
        transcriptionService.reset()
        hasConfiguredCallbacks = false
        pendingTranscript = nil
        transcriptHandler = nil
        isRecording = false
        isTranscribing = false
        isVoiceActionInFlight = false
    }

    private func startRecording() async {
        if !voiceDictationAvailable {
            await checkAndRefreshVoiceDictation()
        }
        guard voiceDictationAvailable else {
            errorMessage = "Microphone is unavailable right now. Try again in a moment."
            return
        }

        configureTranscriptionCallbacks()
        pendingTranscript = nil
        errorMessage = nil
        isVoiceActionInFlight = true
        defer { isVoiceActionInFlight = false }

        do {
            try await transcriptionService.start()
            isRecording = true
            isTranscribing = false
        } catch {
            errorMessage = error.localizedDescription
            isRecording = false
            isTranscribing = false
        }
    }

    private func stopRecording() async {
        guard isRecording else { return }
        isVoiceActionInFlight = true
        defer { isVoiceActionInFlight = false }

        do {
            let transcript = try await transcriptionService.stop()
            pendingTranscript = nil
            isRecording = false
            isTranscribing = false
            applyTranscript(transcript)
        } catch {
            errorMessage = error.localizedDescription
            pendingTranscript = nil
            isRecording = false
            isTranscribing = false
        }
    }

    private func checkAndRefreshVoiceDictation() async {
        if transcriptionService.isAvailable {
            voiceDictationAvailable = true
            return
        }

        voiceDictationAvailable = await OpenAIService.shared.refreshTranscriptionAvailability()
    }

    private func configureTranscriptionCallbacks() {
        hasConfiguredCallbacks = true
        transcriptionService.onTranscriptDelta = nil
        transcriptionService.onTranscriptFinal = { [weak self] transcript in
            self?.pendingTranscript = transcript
        }
        transcriptionService.onStopReason = { [weak self] reason in
            guard let self else { return }
            switch reason {
            case .manual:
                return
            case .silenceAutoStop:
                let transcript = self.pendingTranscript ?? ""
                self.pendingTranscript = nil
                self.isRecording = false
                self.isTranscribing = false
                self.isVoiceActionInFlight = false
                self.applyTranscript(transcript)
            case .cancel, .failure:
                self.pendingTranscript = nil
                self.isRecording = false
                self.isTranscribing = false
                self.isVoiceActionInFlight = false
            }
        }
        transcriptionService.onError = { [weak self] message in
            self?.errorMessage = message
            self?.pendingTranscript = nil
            self?.isRecording = false
            self?.isTranscribing = false
            self?.isVoiceActionInFlight = false
        }
        transcriptionService.onStateChange = { [weak self] state in
            guard let self else { return }
            switch state {
            case .idle:
                self.isRecording = false
                self.isTranscribing = false
            case .recording:
                self.isRecording = true
                self.isTranscribing = false
            case .transcribing:
                self.isRecording = false
                self.isTranscribing = true
            }
        }
    }

    private func applyTranscript(_ transcript: String) {
        let trimmedTranscript = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedTranscript.isEmpty else {
            errorMessage = "I didn't catch that. Try again."
            return
        }

        errorMessage = nil
        transcriptHandler?(trimmedTranscript)
    }
}
