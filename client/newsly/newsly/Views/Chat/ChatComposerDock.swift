//
//  ChatComposerDock.swift
//  newsly
//

import SwiftUI

struct ChatComposerDock: View {
    @Binding var inputText: String
    let isInputFocused: FocusState<Bool>.Binding
    let contextTitle: String
    let isContextPresented: Bool
    let canStartCouncil: Bool
    let isStartingCouncil: Bool
    let isSending: Bool
    let isRecording: Bool
    let isTranscribing: Bool
    let isVoiceActionInFlight: Bool
    let voiceDictationAvailable: Bool
    let providerName: String?
    let onToggleContext: () -> Void
    let onStartCouncil: () -> Void
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
            actionRow
            inputRow
            recordingStatus
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(
            RoundedRectangle(cornerRadius: 26, style: .continuous)
                .fill(Color.surfacePrimary.opacity(0.96))
                .overlay(
                    RoundedRectangle(cornerRadius: 26, style: .continuous)
                        .stroke(Color.outlineVariant.opacity(0.22), lineWidth: 1)
                )
                .shadow(color: .black.opacity(0.04), radius: 10, y: 2)
        )
        .padding(.horizontal, 12)
    }

    private var actionRow: some View {
        HStack(spacing: 8) {
            DockActionButton(
                title: contextTitle,
                systemImage: "sidebar.right",
                isActive: isContextPresented,
                action: onToggleContext
            )

            if canStartCouncil {
                DockActionButton(
                    title: isStartingCouncil ? "Starting Council" : "Council",
                    systemImage: "person.3.sequence.fill",
                    isActive: isStartingCouncil,
                    isDisabled: isStartingCouncil || isSending,
                    action: onStartCouncil
                )
                .accessibilityIdentifier("knowledge.start_council")
            }

            Spacer(minLength: 0)

            if let providerName {
                Text(providerName)
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
    }

    private var inputRow: some View {
        HStack(alignment: .center, spacing: 10) {
            TextField("Message", text: $inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.terracottaBodyMedium)
                .lineLimit(1...5)
                .focused(isInputFocused)
                .accessibilityIdentifier("knowledge.chat_input")
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(Color.surfaceContainerHighest.opacity(0.92))
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

private struct DockActionButton: View {
    let title: String
    let systemImage: String
    var isActive = false
    var isDisabled = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Label(title, systemImage: systemImage)
                .font(.terracottaBodySmall)
                .foregroundStyle(isActive ? Color.chatAccent : Color.onSurfaceSecondary)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(
                    Capsule()
                        .fill(isActive ? Color.chatAccent.opacity(0.14) : Color.surfaceSecondary.opacity(0.72))
                )
                .overlay(
                    Capsule()
                        .stroke(
                            isActive ? Color.chatAccent.opacity(0.28) : Color.outlineVariant.opacity(0.18),
                            lineWidth: 1
                        )
                )
        }
        .buttonStyle(.plain)
        .disabled(isDisabled)
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
            contextTitle: "Context",
            isContextPresented: false,
            canStartCouncil: true,
            isStartingCouncil: false,
            isSending: false,
            isRecording: false,
            isTranscribing: false,
            isVoiceActionInFlight: false,
            voiceDictationAvailable: true,
            providerName: "GPT-5.4",
            onToggleContext: {},
            onStartCouncil: {},
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
