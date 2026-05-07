//
//  ChatComposerDock.swift
//  newsly
//

import SwiftUI

struct ChatComposerDock: View {
    @Binding var inputText: String
    let isInputFocused: FocusState<Bool>.Binding
    let canStartCouncil: Bool
    let canStartDeepResearch: Bool
    let isStartingCouncil: Bool
    let isSending: Bool
    let isRecording: Bool
    let isTranscribing: Bool
    let isVoiceActionInFlight: Bool
    let voiceDictationAvailable: Bool
    let onStartCouncil: () -> Void
    let onStartDeepResearch: () -> Void
    let onToggleVoiceRecording: () -> Void
    let onSend: () -> Void

    private var sendButtonDisabled: Bool {
        inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
        isSending ||
        isRecording ||
        isTranscribing
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            inputRow
            recordingStatus
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(
            RoundedRectangle(cornerRadius: 26, style: .continuous)
                .fill(Color.surfacePrimary)
                .overlay(
                    RoundedRectangle(cornerRadius: 26, style: .continuous)
                        .stroke(Color.outlineVariant.opacity(0.22), lineWidth: 1)
                )
                .shadow(color: .black.opacity(0.04), radius: 10, y: 2)
        )
        .padding(.horizontal, 12)
    }

    private var modeMenu: some View {
        Menu {
            if canStartCouncil {
                Button(action: onStartCouncil) {
                    Label(
                        isStartingCouncil ? "Starting Council…" : "Council",
                        systemImage: "person.3.sequence.fill"
                    )
                }
                .disabled(isStartingCouncil || isSending)
            }

            if canStartDeepResearch {
                Button(action: onStartDeepResearch) {
                    Label("Deep Research", systemImage: "magnifyingglass.circle.fill")
                }
                .disabled(isSending)
            }
        } label: {
            Image(systemName: "plus")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Color.onSurfaceSecondary)
                .frame(width: 38, height: 38)
                .background(
                    Circle()
                        .fill(Color.surfaceSecondary.opacity(0.72))
                )
                .overlay(
                    Circle()
                        .stroke(Color.outlineVariant.opacity(0.18), lineWidth: 1)
                )
        }
        .accessibilityLabel("More actions")
        .accessibilityIdentifier("knowledge.mode_menu")
    }

    private var inputRow: some View {
        HStack(alignment: .center, spacing: 10) {
            if canStartCouncil || canStartDeepResearch {
                modeMenu
            }

            TextField("Message", text: $inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.terracottaBodyMedium)
                .lineLimit(1...5)
                .focused(isInputFocused)
                .accessibilityIdentifier("knowledge.chat_input")
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(Color.surfaceContainerHighest)
                .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .stroke(
                            isRecording ? Color.statusDestructive.opacity(0.6) : Color.outlineVariant.opacity(0.3),
                            lineWidth: 1
                        )
                )
                .frame(maxWidth: .infinity)

            TapToTalkMicButton(
                isEnabled: !isSending && !isVoiceActionInFlight && !isTranscribing,
                isRecording: isRecording,
                isBusy: isVoiceActionInFlight && !isRecording,
                size: 38,
                action: onToggleVoiceRecording
            )
            .opacity(voiceDictationAvailable || isRecording ? 1 : 0.72)
            .accessibilityLabel(isRecording ? "Stop recording" : "Start recording")
            .accessibilityHint(isRecording ? "Tap to stop and transcribe into this chat" : "Tap to dictate into this chat")
            .accessibilityIdentifier("knowledge.chat_mic")

            Button(action: onSend) {
                Group {
                    if isSending {
                        ProgressView()
                            .tint(sendButtonDisabled ? Color.onSurfaceSecondary : .white)
                    } else {
                        Image(systemName: "arrow.up")
                            .font(.system(size: 16, weight: .medium))
                    }
                }
                .foregroundStyle(sendButtonDisabled ? Color.onSurfaceSecondary : .white)
                .frame(width: 38, height: 38, alignment: .center)
                .background(sendButtonDisabled ? Color.surfaceContainer : Color.chatUserBubble)
                .clipShape(Circle())
            }
            .disabled(sendButtonDisabled)
            .accessibilityIdentifier("knowledge.chat_send")
        }
    }

    private var recordingStatus: some View {
        VStack(alignment: .leading, spacing: 4) {
            if isTranscribing {
                HStack(spacing: 4) {
                    ProgressView()
                        .scaleEffect(0.7)
                    Text("Transcribing...")
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
                .transition(.opacity.combined(with: .move(edge: .bottom)))
            }

            if isRecording {
                RecordingIndicator()
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            }
        }
        .animation(.easeOut(duration: 0.2), value: isTranscribing)
        .animation(.easeOut(duration: 0.2), value: isRecording)
    }
}

private struct RecordingIndicator: View {
    @State private var isPulsing = false

    var body: some View {
        HStack(spacing: 6) {
            ZStack {
                Circle()
                    .fill(Color.statusDestructive.opacity(0.18))
                    .frame(width: 18, height: 18)
                    .scaleEffect(isPulsing ? 1.3 : 0.9)

                Circle()
                    .fill(Color.statusDestructive)
                    .frame(width: 8, height: 8)
            }
            .onAppear {
                withAnimation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true)) {
                    isPulsing = true
                }
            }

            Text("Recording. Tap the mic to stop.")
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
    }
}

#if DEBUG
private struct ChatComposerDockPreviewHost: View {
    @State private var inputText = "Ask a follow-up"
    @FocusState private var isInputFocused: Bool

    var body: some View {
        ChatComposerDock(
            inputText: $inputText,
            isInputFocused: $isInputFocused,
            canStartCouncil: true,
            canStartDeepResearch: true,
            isStartingCouncil: false,
            isSending: false,
            isRecording: false,
            isTranscribing: false,
            isVoiceActionInFlight: false,
            voiceDictationAvailable: true,
            onStartCouncil: {},
            onStartDeepResearch: {},
            onToggleVoiceRecording: {},
            onSend: {}
        )
    }
}

#Preview("Chat Composer Dock") {
    ChatComposerDockPreviewHost()
        .padding(.vertical)
        .background(Color.surfacePrimary)
}
#endif
