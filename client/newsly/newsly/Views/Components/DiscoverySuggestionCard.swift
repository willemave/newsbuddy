//
//  DiscoverySuggestionCard.swift
//  newsly
//

import SwiftUI

struct DiscoverySuggestionCard: View {
    let suggestion: DiscoverySuggestion
    let suggestionType: String
    let onTap: () -> Void

    private struct SuggestionMetadata {
        let icon: String
        let color: Color
        let sourceName: String
    }

    private var metadata: SuggestionMetadata {
        switch suggestionType {
        case "feed", "rss":
            return SuggestionMetadata(
                icon: "dot.radiowaves.up.forward",
                color: .blue,
                sourceName: "Feed"
            )
        case "podcast_rss", "podcast":
            return SuggestionMetadata(
                icon: "waveform",
                color: .orange,
                sourceName: "Podcast"
            )
        case "youtube":
            return SuggestionMetadata(
                icon: "play.circle.fill",
                color: .red,
                sourceName: "YouTube"
            )
        default:
            return SuggestionMetadata(
                icon: "doc.text",
                color: .blue,
                sourceName: "Feed"
            )
        }
    }

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 8) {
                // Headline
                Text(suggestion.displayTitle)
                    .font(.feedHeadline)
                    .foregroundColor(.textPrimary)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)

                // Metadata bar: type icon + source name + dot + URL
                HStack(spacing: 6) {
                    Image(systemName: metadata.icon)
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(metadata.color)

                    Text(metadata.sourceName.uppercased())
                        .font(.feedMeta)
                        .tracking(0.4)
                        .foregroundColor(.textSecondary)
                        .lineLimit(1)

                    Text("\u{00B7}")
                        .font(.feedMeta)
                        .foregroundColor(.textTertiary)

                    Text(formattedURL(suggestion.primaryURL))
                        .font(.feedMeta)
                        .foregroundColor(.textTertiary)
                        .lineLimit(1)

                    Spacer()
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemGroupedBackground))
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.editorialBorder, lineWidth: 1)
            )
            .cornerRadius(12)
            .shadow(color: .black.opacity(0.03), radius: 4, x: 0, y: 2)
        }
        .buttonStyle(EditorialCardButtonStyle())
    }

    private func formattedURL(_ urlString: String) -> String {
        guard let url = URL(string: urlString),
              let host = url.host else {
            return urlString
        }
        return host.replacingOccurrences(of: "www.", with: "")
    }
}

// MARK: - Card Button Style (press feedback)

struct EditorialCardButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1.0)
            .animation(.spring(response: 0.28, dampingFraction: 0.82), value: configuration.isPressed)
    }
}
