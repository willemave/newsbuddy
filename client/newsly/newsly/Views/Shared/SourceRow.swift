//
//  SourceRow.swift
//  newsly
//
//  Row component for feed and podcast sources with status chip.
//

import SwiftUI

struct SourceRow: View {
    let name: String
    let url: String?
    let type: String
    let isActive: Bool
    let stats: ScraperConfigStats?

    var body: some View {
        HStack(spacing: 12) {
            // Type icon
            SourceTypeIcon(type: type)
                .accessibilityHidden(true)

            // Content
            VStack(alignment: .leading, spacing: 2) {
                Text(name)
                    .font(.listTitle)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(1)

                if let url {
                    Text(formattedURL(url))
                        .font(.listValue)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(1)
                }

                if let summary = statsLine {
                    Text(summary)
                        .font(.appCaption)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(2)
                }
            }

            Spacer(minLength: 8)

            // Status + chevron
            HStack(spacing: 8) {
                StatusChip(isActive: isActive)
                    .accessibilityHidden(true)

                Image(systemName: "chevron.right")
                    .font(.appSymbol(size: 12, weight: .semibold))
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .accessibilityHidden(true)
            }
        }
        .padding(.vertical, Spacing.rowVertical)
        .padding(.horizontal, Spacing.rowHorizontal)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(sourceAccessibilityLabel)
        .accessibilityAddTraits(.isButton)
    }

    private func formattedURL(_ urlString: String) -> String {
        guard let url = URL(string: urlString), let host = url.host else {
            return urlString
        }
        return host.replacingOccurrences(of: "www.", with: "")
    }

    private var statsLine: String? {
        guard let stats, stats.hasVisibleStats else { return nil }

        var parts: [String] = []
        if let countSummary = stats.compactCountSummary {
            parts.append(countSummary)
        }
        if let processedSummary = stats.relativeProcessedSummary {
            parts.append(processedSummary)
        }
        return parts.isEmpty ? nil : parts.joined(separator: " • ")
    }

    private var sourceAccessibilityLabel: String {
        var parts = [name]
        if let url {
            parts.append(formattedURL(url))
        }
        parts.append(isActive ? "Active" : "Inactive")
        if let statsLine {
            parts.append(statsLine)
        }
        return parts.joined(separator: ", ")
    }
}

// MARK: - Source Type Icon

struct SourceTypeIcon: View {
    let type: String

    private var metadata: SourceVisualMetadata { .sourceType(type) }

    var body: some View {
        switch metadata.glyph {
        case .system(let name):
            Image(systemName: name)
                .font(.appSymbol(size: 17, weight: .medium))
                .foregroundStyle(metadata.color)
                .frame(width: Spacing.iconSize, height: Spacing.iconSize)
        case .text(let value):
            Text(value)
                .font(.appSans(size: 13, weight: .bold))
                .foregroundStyle(metadata.color)
                .frame(width: Spacing.iconSize, height: Spacing.iconSize)
        }
    }
}

#Preview {
    VStack(spacing: 0) {
        SourceRow(
            name: "Stratechery",
            url: "https://stratechery.com/feed",
            type: "substack",
            isActive: true,
            stats: nil
        )

        Divider().padding(.leading, 56)

        SourceRow(
            name: "The Vergecast",
            url: "https://feeds.megaphone.fm/vergecast",
            type: "podcast_rss",
            isActive: true,
            stats: nil
        )

        Divider().padding(.leading, 56)

        SourceRow(
            name: "MKBHD",
            url: "https://youtube.com/mkbhd",
            type: "youtube",
            isActive: false,
            stats: nil
        )
    }
    .background(Color.surfacePrimary)
}
