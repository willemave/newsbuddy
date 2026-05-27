//
//  FastReadBriefingComponents.swift
//  newsly
//
//  Components and display helpers for the Fast Read surface.
//

import Foundation
import SwiftUI

enum FastReadPresentation {
    static func summaryText(for item: ContentSummary) -> String? {
        if let newsSummary = normalizedText(item.newsSummary) {
            return newsSummary
        }

        if let keyPoints = item.newsKeyPoints?.compactMap({ normalizedText($0) }), !keyPoints.isEmpty {
            return keyPoints.prefix(2).joined(separator: " ")
        }

        if let previewBullets = item.previewBullets?.compactMap({ normalizedText($0) }), !previewBullets.isEmpty {
            return previewBullets.prefix(2).joined(separator: " ")
        }

        return normalizedText(item.shortSummary)
    }

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

struct BriefingStackCard: View {
    let items: [ContentSummary]
    let durationSeconds: Int?
    let isPreparing: Bool
    let isPlaying: Bool
    let onPlay: () -> Void

    private var unreadItems: [ContentSummary] {
        items.filter { !$0.isRead }
    }

    private var includedCount: Int {
        let availableCount = unreadItems.isEmpty ? items.count : unreadItems.count
        return min(availableCount, 15)
    }

    private var statusText: String {
        if unreadItems.isEmpty {
            return "LATEST STORIES READY FOR REVIEW"
        }
        return "\(includedCount) UNREAD FOR AUDIO"
    }

    private var durationText: String {
        guard let durationSeconds, durationSeconds > 0 else {
            return "~1 MIN"
        }
        let minutes = max(1, Int((Double(durationSeconds) / 60.0).rounded()))
        return "~\(minutes) MIN"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .center, spacing: 14) {
                Image(systemName: "square.stack.3d.up.fill")
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundStyle(Color.brandSecondary.opacity(0.86))
                    .frame(width: 44, height: 44)
                    .accessibilityHidden(true)

                VStack(alignment: .leading, spacing: 5) {
                    Text("Briefing Stack")
                        .font(.terracottaHeadlineLarge)
                        .foregroundStyle(Color.onSurface)
                        .lineLimit(1)
                        .minimumScaleFactor(0.82)

                    Text(statusText)
                        .font(.terracottaCategoryPill)
                        .tracking(1.6)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .layoutPriority(1)

                Button(action: onPlay) {
                    ZStack {
                        Circle()
                            .fill(Color.surfacePrimary.opacity(0.6))
                            .frame(width: 58, height: 58)
                            .overlay {
                                Circle()
                                    .stroke(Color.brandPrimary.opacity(0.92), lineWidth: 1.2)
                            }

                        if isPreparing {
                            ProgressView()
                                .controlSize(.small)
                                .tint(Color.brandPrimary)
                        } else {
                            Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                                .font(.system(size: 20, weight: .bold))
                                .foregroundStyle(Color.onSurface)
                                .offset(x: isPlaying ? 0 : 2)
                        }
                    }
                }
                .buttonStyle(.plain)
                .disabled(isPreparing)
                .accessibilityLabel(isPlaying ? "Pause Fast Reads Brief" : "Play Fast Reads Brief")
                .accessibilityIdentifier("short.audio.briefing_stack")
            }

            HStack {
                Text(isPreparing ? "PREPARING YOUR BRIEFING" : "TAP PLAY TO HEAR YOUR BRIEFING")
                    .font(.terracottaCategoryPill)
                    .tracking(1.7)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)

                Spacer()

                Text(durationText)
                    .font(.terracottaCategoryPill)
                    .tracking(1.5)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .lineLimit(1)
                    .monospacedDigit()
            }
        }
        .padding(.horizontal, 22)
        .padding(.vertical, 18)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Color.surfaceSecondary.opacity(0.58))
        )
        .overlay {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(Color.brandPrimary.opacity(0.30), lineWidth: 1)
        }
        .shadow(color: Color.brandPrimary.opacity(0.08), radius: 24, x: 0, y: 10)
        .accessibilityElement(children: .contain)
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
                    .font(.system(size: 13, weight: .semibold))
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
                    .font(.system(size: 13, weight: .semibold))
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
