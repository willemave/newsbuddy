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
                        LongFormActionChip(
                            title: isCustomNarrationGenerating ? "Creating narration" : "Create narration",
                            systemImage: "waveform",
                            isLoading: isCustomNarrationGenerating
                        )
                    }
                    .buttonStyle(.plain)
                    .disabled(isCustomNarrationGenerating)
                    .accessibilityIdentifier("long.custom_narration.create")

                    Button(action: onShowNarrations) {
                        LongFormActionChip(
                            title: "List narrations",
                            systemImage: "list.bullet.rectangle",
                            isLoading: false
                        )
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("long.custom_narration.list")

                    Button(action: onSummarizeRecent) {
                        LongFormActionChip(
                            title: "Summarize recent",
                            systemImage: "text.bubble",
                            isLoading: isStartingSummaryChat
                        )
                    }
                    .buttonStyle(.plain)
                    .disabled(isStartingSummaryChat)
                    .accessibilityIdentifier("long.quick_action.summarize_recent")
                }
                .padding(.horizontal, Spacing.screenHorizontal)
            }

            if let customNarrationError {
                errorText(customNarrationError)
            }

            if let summaryError {
                errorText(summaryError)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func errorText(_ message: String) -> some View {
        Text(message)
            .font(.terracottaBodySmall)
            .foregroundStyle(.red)
            .lineLimit(2)
            .padding(.horizontal, Spacing.screenHorizontal)
    }
}

private struct LongFormActionChip: View {
    let title: String
    let systemImage: String
    let isLoading: Bool

    var body: some View {
        HStack(spacing: 8) {
            if isLoading {
                ProgressView()
                    .controlSize(.small)
                    .tint(Color.terracottaPrimary)
            } else {
                Image(systemName: systemImage)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Color.terracottaPrimary)
            }

            Text(title)
                .font(.terracottaBodyMedium.weight(.semibold))
                .foregroundStyle(Color.onSurface)
                .lineLimit(1)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Color.surfaceSecondary)
        .clipShape(Capsule())
        .overlay {
            Capsule()
                .stroke(Color.outlineVariant.opacity(0.3), lineWidth: 1)
        }
    }
}
