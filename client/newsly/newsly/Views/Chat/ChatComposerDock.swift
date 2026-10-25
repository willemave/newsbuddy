//
//  ChatComposerDock.swift
//  newsly
//

import SwiftUI

struct ChatComposerDock: View {
    @Binding var inputText: String
    let isInputFocused: FocusState<Bool>.Binding
    let session: ChatSessionSummary?
    let isSending: Bool
    let isRecording: Bool
    let isTranscribing: Bool
    let isVoiceActionInFlight: Bool
    let voiceDictationAvailable: Bool
    let onShowHistory: (() -> Void)?
    let onSwitchProvider: (ChatModelProvider) -> Void
    let onToggleVoiceRecording: () -> Void
    let onSend: () -> Void

    private var sendButtonDisabled: Bool {
        inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
        isRecording ||
        isTranscribing
    }

    private var showsSendAction: Bool {
        !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !isRecording
            && !isTranscribing
    }

    private var showsRecordingStatus: Bool {
        isTranscribing || isRecording
    }

    private var showsMoreActionsMenu: Bool {
        onShowHistory != nil || session != nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if session?.isRetiredDeepResearch == true {
                Text("Deep Research has been retired. Start a regular chat to research a topic.")
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .padding(8)
            } else {
                inputRow
            }
            if showsRecordingStatus {
                recordingStatus
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        // One glass dock that messages scroll beneath; it warms toward the
        // destructive tint while recording instead of outlining the field.
        .glassEffect(
            isRecording ? .regular.tint(Color.statusDestructive.opacity(0.18)) : .floatingChrome,
            in: RoundedRectangle(cornerRadius: 28, style: .continuous)
        )
        .animation(AppMotion.subtle, value: isRecording)
        .padding(.horizontal, Spacing.appHorizontalMargin)
    }

    private var moreActionsMenu: some View {
        Menu {
            if let onShowHistory {
                Section {
                    Button(action: onShowHistory) {
                        Label("Chat History", systemImage: "clock.arrow.circlepath")
                    }
                    .accessibilityIdentifier("knowledge.chat_history")
                }
            }

            if let session, !session.isRetiredDeepResearch {
                Section("Model") {
                    Label("Current: \(session.providerDisplayName)", systemImage: session.providerIconFallback)
                        .foregroundStyle(Color.onSurfaceSecondary)

                    ForEach(ChatModelProvider.selectableProviders, id: \.self) { provider in
                        Button {
                            onSwitchProvider(provider)
                        } label: {
                            Label(provider.chatDisplayName, systemImage: provider.iconName)
                        }
                        .disabled(provider.rawValue == session.llmProvider)
                    }
                }
            }
        } label: {
            // The buddy stands in for the menu glyph — this is where the app has a face.
            // Rendered at original intent so it keeps its own indigo rather than tinting.
            Image("BuddyMark")
                .resizable()
                .renderingMode(.original)
                .scaledToFit()
                .frame(width: 22, height: 22)
                .frame(width: 44, height: 44)
                // A soft fill, not glass: the button sits inside the dock's glass.
                .background(Circle().fill(Color.onSurface.opacity(0.08)))
        }
        .contentShape(Circle())
        .accessibilityLabel("More actions")
        .accessibilityIdentifier("knowledge.mode_menu")
    }

    private var inputRow: some View {
        HStack(alignment: .center, spacing: 8) {
            if showsMoreActionsMenu {
                moreActionsMenu
            }

            TextField("Message", text: $inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.terracottaBodyMedium)
                .lineLimit(1...5)
                .focused(isInputFocused)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                // Bare field: the dock's glass is the container.
                .accessibilityLabel("Message")
                .accessibilityIdentifier("knowledge.chat_input")

            Group {
                if showsSendAction {
                    sendButton
                        .transition(.opacity.combined(with: .scale(scale: 0.96)))
                } else {
                    voiceButton
                        .transition(.opacity.combined(with: .scale(scale: 0.96)))
                }
            }
            .animation(AppMotion.subtle, value: showsSendAction)
        }
    }

    private var voiceButton: some View {
        TapToTalkMicButton(
            isEnabled: !isVoiceActionInFlight && !isTranscribing,
            isRecording: isRecording,
            isTranscribing: isTranscribing,
            isBusy: isVoiceActionInFlight && !isRecording,
            size: 44,
            action: onToggleVoiceRecording
        )
        .opacity(voiceDictationAvailable || isRecording ? 1 : 0.72)
        .accessibilityLabel(isRecording ? "Stop recording" : "Start recording")
        .accessibilityHint(isRecording ? "Tap to stop and send this chat message" : "Tap to dictate and send into this chat")
        .accessibilityIdentifier("knowledge.chat_mic")
        .accessibilityValue(voiceAccessibilityValue)
    }

    private var voiceAccessibilityValue: String {
        if isTranscribing { return "Transcribing" }
        if isRecording { return "Recording" }
        if isVoiceActionInFlight { return "Starting" }
        return "Idle"
    }

    private var sendButton: some View {
        Button(action: onSend) {
            Image(systemName: "arrow.up")
                .font(.appSymbol(size: 16, weight: .semibold))
                .foregroundStyle(sendButtonDisabled ? Color.onSurfaceSecondary : Color.chatUserBubbleText)
                .frame(width: 44, height: 44, alignment: .center)
                .background(sendButtonDisabled ? Color.surfaceContainer : Color.chatUserBubble)
                .clipShape(Circle())
        }
        .disabled(sendButtonDisabled)
        .contentShape(Circle())
        .accessibilityLabel(isSending ? "Queue message" : "Send message")
        .accessibilityIdentifier("knowledge.chat_send")
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
        .animation(AppMotion.subtle, value: isTranscribing)
        .animation(AppMotion.subtle, value: isRecording)
    }
}

private struct RecordingIndicator: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isPulsing = false

    var body: some View {
        HStack(spacing: 6) {
            ZStack {
                Circle()
                    .fill(Color.statusDestructive.opacity(0.18))
                    .frame(width: 18, height: 18)
                    .scaleEffect(reduceMotion ? 1.0 : (isPulsing ? 1.3 : 0.9))

                Circle()
                    .fill(Color.statusDestructive)
                    .frame(width: 8, height: 8)
            }
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(AppMotion.voiceLevelPulse) {
                    isPulsing = true
                }
            }
            .onChange(of: reduceMotion) { _, reduceMotion in
                if reduceMotion {
                    isPulsing = false
                } else {
                    withAnimation(AppMotion.voiceLevelPulse) {
                        isPulsing = true
                    }
                }
            }

            Text("Recording. Tap the mic to send.")
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
            session: ChatPreviewFixtures.session,
            isSending: false,
            isRecording: false,
            isTranscribing: false,
            isVoiceActionInFlight: false,
            voiceDictationAvailable: true,
            onShowHistory: {},
            onSwitchProvider: { _ in },
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
