//
//  LearningDeckChatComposer.swift
//  newsly
//

import SwiftUI

struct LearningDeckChatComposer: View {
    @Bindable var viewModel: LearningDeckReaderViewModel

    @FocusState private var isInputFocused: Bool

    var body: some View {
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

            Button(action: sendMessage) {
                ZStack {
                    Circle()
                        .fill(Color.surfacePrimary.opacity(0.001))
                    if viewModel.isSending {
                        ProgressView()
                            .tint(sendButtonDisabled ? Color.onSurfaceSecondary : Color.chatUserBubbleText)
                            .transition(.opacity)
                    } else {
                        Image(systemName: "arrow.up")
                            .font(.appSymbol(size: 16, weight: .semibold))
                            .transition(.opacity)
                    }
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
            .accessibilityLabel(viewModel.isSending ? "Sending message" : "Send message")
            .accessibilityIdentifier("learning_deck.chat.send")
        }
    }

    private var sendButtonDisabled: Bool {
        viewModel.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
            viewModel.isSending
    }

    private func sendMessage() {
        guard !sendButtonDisabled else { return }
        viewModel.performSendMessage()
    }
}
