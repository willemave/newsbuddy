//
//  ChatComposerDock.swift
//  newsly
//

import SwiftUI

struct ChatComposerDock: View {
    @Binding var inputText: String
    let isInputFocused: FocusState<Bool>.Binding
    let session: ChatSessionSummary?
    let canStartCouncil: Bool
    let canStartDeepResearch: Bool
    let isStartingCouncil: Bool
    let isSending: Bool
    let isRecording: Bool
    let isTranscribing: Bool
    let isVoiceActionInFlight: Bool
    let voiceDictationAvailable: Bool
    let onShowHistory: (() -> Void)?
    let onSwitchProvider: (ChatModelProvider) -> Void
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

    private var showsRecordingStatus: Bool {
        isTranscribing || isRecording
    }

    private var showsMoreActionsMenu: Bool {
        onShowHistory != nil ||
        session != nil ||
        canStartCouncil ||
        canStartDeepResearch
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            inputRow
            if showsRecordingStatus {
                recordingStatus
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(Color.surfacePrimary)
                .overlay(
                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .stroke(Color.outlineVariant.opacity(0.22), lineWidth: 1)
                )
                .shadow(color: .black.opacity(0.04), radius: 8, y: 2)
        )
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

            if let session {
                Section("Model") {
                    Label("Current: \(session.providerDisplayName)", systemImage: session.providerIconFallback)
                        .foregroundStyle(Color.onSurfaceSecondary)

                    if session.isCouncilMode {
                        Label("Switching unavailable in Council", systemImage: "person.3.sequence.fill")
                            .disabled(true)
                    } else {
                        ForEach(ChatModelProvider.allCases.filter { !$0.isDeepResearch }, id: \.self) { provider in
                            Button {
                                onSwitchProvider(provider)
                            } label: {
                                Label(provider.chatDisplayName, systemImage: provider.iconName)
                            }
                            .disabled(provider.rawValue == session.llmProvider)
                        }
                    }
                }
            }

            if canStartCouncil || canStartDeepResearch {
                Section("Actions") {
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
                }
            }
        } label: {
            Image(systemName: "plus")
                .font(.appSymbol(size: 16, weight: .semibold))
                .foregroundStyle(Color.onSurfaceSecondary)
                .frame(width: 44, height: 44)
                .background(
                    Circle()
                        .fill(Color.surfaceSecondary.opacity(0.72))
                )
                .overlay(
                    Circle()
                        .stroke(Color.outlineVariant.opacity(0.18), lineWidth: 1)
                )
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
                .background(Color.surfaceContainerHighest)
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .stroke(
                            isRecording ? Color.statusDestructive.opacity(0.6) : Color.outlineVariant.opacity(0.3),
                            lineWidth: 1
                        )
                )
                .accessibilityLabel("Message")
                .accessibilityIdentifier("knowledge.chat_input")

            TapToTalkMicButton(
                isEnabled: !isSending && !isVoiceActionInFlight && !isTranscribing,
                isRecording: isRecording,
                isBusy: isVoiceActionInFlight && !isRecording,
                size: 44,
                action: onToggleVoiceRecording
            )
            .opacity(voiceDictationAvailable || isRecording ? 1 : 0.72)
            .accessibilityLabel(isRecording ? "Stop recording" : "Start recording")
            .accessibilityHint(isRecording ? "Tap to stop and send this chat message" : "Tap to dictate and send into this chat")
            .accessibilityIdentifier("knowledge.chat_mic")

            Button(action: onSend) {
                Group {
                    if isSending {
                        ProgressView()
                            .tint(sendButtonDisabled ? Color.onSurfaceSecondary : Color.chatUserBubbleText)
                    } else {
                        Image(systemName: "arrow.up")
                            .font(.appSymbol(size: 16, weight: .medium))
                    }
                }
                .foregroundStyle(sendButtonDisabled ? Color.onSurfaceSecondary : Color.chatUserBubbleText)
                .frame(width: 44, height: 44, alignment: .center)
                .background(sendButtonDisabled ? Color.surfaceContainer : Color.chatUserBubble)
                .clipShape(Circle())
            }
            .disabled(sendButtonDisabled)
            .contentShape(Circle())
            .accessibilityLabel(isSending ? "Sending message" : "Send message")
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
            canStartCouncil: true,
            canStartDeepResearch: true,
            isStartingCouncil: false,
            isSending: false,
            isRecording: false,
            isTranscribing: false,
            isVoiceActionInFlight: false,
            voiceDictationAvailable: true,
            onShowHistory: {},
            onSwitchProvider: { _ in },
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
