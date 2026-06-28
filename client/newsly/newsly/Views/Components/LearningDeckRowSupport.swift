//
//  LearningDeckRowSupport.swift
//  newsly
//

import SwiftUI

enum DeckRowMetrics {
    static let surfaceRadius: CGFloat = 18
    static let iconRadius: CGFloat = 12
    static let contentPadding: CGFloat = 12
    static let rowVerticalPadding: CGFloat = 5
    static let iconSize: CGFloat = 38
}

struct LearningDeckLoadingRow: View {
    var body: some View {
        HStack(spacing: 10) {
            ProgressView()
                .controlSize(.small)
            Text("Loading decks")
                .font(.terracottaBodyMedium)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .padding(.vertical, 10)
    }
}

struct LearningDeckEmptyRow: View {
    var onCreate: (() -> Void)?

    var body: some View {
        VStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: DeckRowMetrics.iconRadius, style: .continuous)
                    .fill(Color.surfaceSecondary)
                Image(systemName: "rectangle.stack")
                    .font(.appSymbol(size: 20, weight: .semibold))
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .frame(width: 46, height: 46)

            VStack(spacing: 4) {
                Text("No decks yet")
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(Color.onSurface)
                Text("Turn any article into a quick deck you can flip through and chat with.")
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let onCreate {
                Button(action: onCreate) {
                    Text("Create a deck")
                        .font(.terracottaBodyMedium.weight(.semibold))
                        .foregroundStyle(Color.brandPrimary)
                        .padding(.horizontal, 16)
                        .frame(minHeight: 44)
                        .background(Color.surfaceSecondary, in: Capsule())
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("learning_deck.empty.create")
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
    }
}

extension View {
    func learningDeckRowSurface() -> some View {
        self
            .background(
                .ultraThinMaterial,
                in: RoundedRectangle(cornerRadius: DeckRowMetrics.surfaceRadius, style: .continuous)
            )
            .overlay {
                RoundedRectangle(cornerRadius: DeckRowMetrics.surfaceRadius, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.22), lineWidth: 1)
            }
            .shadow(color: .black.opacity(0.04), radius: 10, y: 4)
    }

    @ViewBuilder
    func learningDeckIconSurface() -> some View {
        glassSurface(
            in: RoundedRectangle(cornerRadius: DeckRowMetrics.iconRadius, style: .continuous),
            tint: Color.surfaceSecondary,
            opacity: 0.14,
            fallback: .none
        )
    }

    @ViewBuilder
    func learningDeckStatusSurface(tint: Color) -> some View {
        glassSurface(
            in: Capsule(),
            tint: tint,
            opacity: 0.12,
            fallback: .tint(opacity: 0.11)
        )
    }
}
