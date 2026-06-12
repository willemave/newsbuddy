//
//  LongFormActionsView.swift
//  newsly
//

import SwiftUI

struct LongFormActionsView: View {
    let isCustomNarrationGenerating: Bool
    let customNarrationError: String?
    let isStartingSummaryChat: Bool
    let summaryError: String?
    let onCreateNarration: () -> Void
    let onShowNarrations: () -> Void
    let onSummarizeRecent: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    Button(action: onCreateNarration) {
                        FeedActionChip(
                            title: isCustomNarrationGenerating ? "Creating narration" : "Create narration",
                            systemImage: "waveform",
                            isLoading: isCustomNarrationGenerating
                        )
                    }
                    .buttonStyle(EditorialCardButtonStyle())
                    .disabled(isCustomNarrationGenerating)
                    .accessibilityIdentifier("long.custom_narration.create")

                    Button(action: onShowNarrations) {
                        FeedActionChip(
                            title: "List narrations",
                            systemImage: "list.bullet.rectangle"
                        )
                    }
                    .buttonStyle(EditorialCardButtonStyle())
                    .accessibilityIdentifier("long.custom_narration.list")

                    Button(action: onSummarizeRecent) {
                        FeedActionChip(
                            title: "Summarize recent",
                            systemImage: "text.bubble",
                            isLoading: isStartingSummaryChat
                        )
                    }
                    .buttonStyle(EditorialCardButtonStyle())
                    .disabled(isStartingSummaryChat)
                    .accessibilityIdentifier("long.quick_action.summarize_recent")
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
            }

            if let customNarrationError {
                errorText(customNarrationError)
            }

            if let summaryError {
                errorText(summaryError)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .animation(.easeOut(duration: 0.2), value: customNarrationError)
        .animation(.easeOut(duration: 0.2), value: summaryError)
    }

    private func errorText(_ message: String) -> some View {
        Text(message)
            .font(.terracottaBodySmall)
            .foregroundStyle(Color.statusDestructive)
            .lineLimit(2)
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .transition(.opacity)
    }
}
