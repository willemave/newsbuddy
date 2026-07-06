//
//  KnowledgeLibrarySection.swift
//  newsly
//

import SwiftUI

struct KnowledgeLibrarySection: View {
    let onShowKnowledgeLibrary: (() -> Void)?
    let onShowNarrations: () -> Void
    let onShowLearningDecks: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionHeader("Library")

            HStack(spacing: 8) {
                libraryTile(
                    title: "Saved",
                    systemImage: "books.vertical.fill",
                    action: { onShowKnowledgeLibrary?() }
                )
                .disabled(onShowKnowledgeLibrary == nil)

                libraryTile(
                    title: "Narration",
                    systemImage: "waveform",
                    action: onShowNarrations
                )

                libraryTile(
                    title: "Learning Decks",
                    systemImage: "rectangle.stack.fill",
                    action: onShowLearningDecks
                )
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
        }
        .padding(.bottom, 24)
    }

    private func libraryTile(
        title: String,
        systemImage: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top) {
                    actionIcon(systemImage)

                    Spacer(minLength: 0)

                    Image(systemName: "arrow.right")
                        .font(.appSymbol(size: 11, weight: .semibold))
                        .foregroundStyle(Color.onSurfaceSecondary)
                }

                Text(title)
                    .font(.terracottaBodyMedium.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
            }
            .padding(12)
            .frame(maxWidth: .infinity, minHeight: 84, alignment: .topLeading)
            .background(Color.surfaceSecondary)
            .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.3), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    private func actionIcon(_ systemName: String) -> some View {
        Image(systemName: systemName)
            .font(.appSymbol(size: 15, weight: .semibold))
            .foregroundColor(.brandPrimary)
            .frame(width: 32, height: 32)
            .background(Color.brandPrimary.opacity(0.14))
            .clipShape(RoundedRectangle(cornerRadius: CornerRadius.nestedControl, style: .continuous))
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title.uppercased())
            .kicker()
            .accessibilityLabel(title)
            .padding(.horizontal, Spacing.appHorizontalMargin)
    }
}
