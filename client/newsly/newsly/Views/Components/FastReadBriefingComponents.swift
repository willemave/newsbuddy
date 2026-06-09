//
//  FastReadBriefingComponents.swift
//  newsly
//
//  Components and display helpers for the Fast Read surface.
//

import Foundation
import SwiftUI

enum FastReadPresentation {
    static func sourceLabel(for item: ContentSummary) -> String? {
        if let platform = normalizedText(item.platform) {
            return platform.uppercased()
        }
        if let source = normalizedText(item.source) {
            return source.uppercased()
        }
        return nil
    }

    private static func normalizedText(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

struct ShortNewsAudioActionChip: View {
    let isLoading: Bool
    let isPlaying: Bool

    var body: some View {
        HStack(spacing: 8) {
            if isLoading {
                ProgressView()
                    .controlSize(.small)
                    .tint(Color.brandPrimary)
            } else {
                Image(systemName: isPlaying ? "pause.fill" : "waveform")
                    .font(.appSymbol(size: 13, weight: .semibold))
                    .foregroundStyle(Color.brandPrimary)
            }

            Text("Audio Brief")
                .font(.terracottaBodyMedium.weight(.semibold))
                .foregroundStyle(Color.onSurface)
                .lineLimit(1)
        }
        .fastReadChipSurface()
    }
}

struct ShortNewsQuickAction: Identifiable {
    let id: String
    let title: String
    let systemImage: String
    let prompt: String
    let screenContext: AssistantScreenContext
}

struct ShortNewsQuickActionChip: View {
    let action: ShortNewsQuickAction
    let isLoading: Bool

    var body: some View {
        HStack(spacing: 8) {
            if isLoading {
                ProgressView()
                    .controlSize(.small)
                    .tint(Color.brandPrimary)
            } else {
                Image(systemName: action.systemImage)
                    .font(.appSymbol(size: 13, weight: .semibold))
                    .foregroundStyle(Color.brandPrimary)
            }

            Text(action.title)
                .font(.terracottaBodyMedium.weight(.semibold))
                .foregroundStyle(Color.onSurface)
                .lineLimit(1)
        }
        .fastReadChipSurface()
    }
}

private extension View {
    func fastReadChipSurface() -> some View {
        frame(minHeight: 44)
            .padding(.horizontal, 16)
            .background(Color.surfaceSecondary.opacity(0.74))
            .clipShape(Capsule())
            .overlay {
                Capsule()
                    .stroke(Color.brandPrimary.opacity(0.26), lineWidth: 1)
            }
            .shadow(color: Color.brandPrimary.opacity(0.05), radius: 12, x: 0, y: 5)
    }
}
