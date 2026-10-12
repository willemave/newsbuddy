//
//  LearningDeckChatComposer.swift
//  newsly
//

import SwiftUI

struct LearningDeckChatComposer: View {
    @Bindable var viewModel: LearningDeckReaderViewModel

    @FocusState private var isInputFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .bottom, spacing: 8) {
                TextField("Ask about this slide", text: $viewModel.inputText, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.terracottaBodyMedium)
                    .lineLimit(1...4)
                    .focused($isInputFocused)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .learningDeckReaderInputSurface(isFocused: isInputFocused)
                    .submitLabel(.send)
                    .onSubmit(sendMessage)
                    .accessibilityIdentifier("learning_deck.chat.input")

                LearningDeckChatVoiceButton(viewModel: viewModel) {
                    isInputFocused = false
                    viewModel.performToggleVoiceRecording()
                }

                Button(action: sendMessage) {
                    ZStack {
                        Circle()
                            .fill(Color.surfacePrimary.opacity(0.001))
                        Image(systemName: "arrow.up")
                            .font(.appSymbol(size: 16, weight: .semibold))
                            .transition(.opacity)
                    }
                    .foregroundStyle(sendButtonDisabled ? Color.onSurfaceSecondary : Color.chatUserBubbleText)
                    .animation(AppMotion.subtle, value: sendButtonDisabled)
                    .animation(AppMotion.subtle, value: viewModel.isSending)
                    .frame(width: 44, height: 44)
                    .learningDeckReaderSendSurface(isEnabled: !sendButtonDisabled)
                    .contentShape(Circle())
                }
                .buttonStyle(PressableButtonStyle())
                .disabled(sendButtonDisabled)
                .accessibilityLabel(viewModel.isSending ? "Queue message" : "Send message")
                .accessibilityIdentifier("learning_deck.chat.send")
            }

            if viewModel.isVoiceRecording || viewModel.isVoiceTranscribing {
                Text(viewModel.isVoiceRecording ? "Listening… Tap stop to send." : "Transcribing…")
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .accessibilityIdentifier("learning_deck.chat.voice_status")
            }
        }
    }

    private var sendButtonDisabled: Bool {
        viewModel.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || viewModel.isVoiceRecording || viewModel.isVoiceTranscribing
    }

    private func sendMessage() {
        guard !sendButtonDisabled else { return }
        viewModel.performSendMessage()
    }
}

struct LearningDeckChatVoiceButton: View {
    let viewModel: LearningDeckReaderViewModel
    let onToggle: () -> Void

    var body: some View {
        Button(action: onToggle) {
            Group {
                if viewModel.isVoiceTranscribing || viewModel.voiceState == .starting {
                    ProgressView().tint(Color.brandPrimary)
                } else {
                    Image(systemName: viewModel.isVoiceRecording ? "stop.fill" : "mic.fill")
                        .font(.appSymbol(size: 18, weight: .semibold))
                        .foregroundStyle(Color.brandPrimary)
                }
            }
            .frame(width: 44, height: 44)
            .learningDeckReaderCircleSurface(tint: Color.brandPrimary, isEnabled: true)
            .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .disabled(viewModel.voiceInput.isActionInFlight || viewModel.isVoiceTranscribing)
        .accessibilityLabel(viewModel.isVoiceRecording ? "Stop voice recording" : "Ask by voice")
        .accessibilityValue(viewModel.isVoiceTranscribing ? "Transcribing" : "")
        .accessibilityIdentifier("learning_deck.chat.voice")
    }
}
