//
//  OnboardingSuggestionCard.swift
//  newsly
//
//  Extracted from OnboardingFlowView for reuse in DiscoveryPersonalizeSheet.
//

import SwiftUI

struct OnboardingSuggestionCard: View {
    let suggestion: OnboardingSuggestion
    let isSelected: Bool
    let onToggle: () -> Void

    private struct SuggestionMetadata {
        let icon: String
        let accentColor: Color
        let label: String
    }

    private var metadata: SuggestionMetadata {
        switch suggestion.suggestionType {
        case "substack", "newsletter":
            return SuggestionMetadata(
                icon: "envelope.open",
                accentColor: .watercolorMistyBlue,
                label: "Newsletter"
            )
        case "podcast_rss", "podcast":
            return SuggestionMetadata(
                icon: "waveform",
                accentColor: .watercolorDiffusedPeach,
                label: "Podcast"
            )
        case "reddit":
            return SuggestionMetadata(
                icon: "bubble.left.and.text.bubble.right",
                accentColor: .watercolorPaleEmerald,
                label: "Reddit"
            )
        default:
            return SuggestionMetadata(
                icon: "doc.text",
                accentColor: .watercolorSoftSky,
                label: "Feed"
            )
        }
    }

    var body: some View {
        Button(action: onToggle) {
            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        Image(systemName: metadata.icon)
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundColor(metadata.accentColor)

                        Text(metadata.label.uppercased())
                            .font(.caption.weight(.semibold))
                            .tracking(1.0)
                            .foregroundColor(.watercolorSlate.opacity(0.62))

                        if let sourceDetail, !sourceDetail.isEmpty {
                            Text(".")
                                .font(.caption)
                                .foregroundColor(.watercolorSlate.opacity(0.38))

                            Text(sourceDetail)
                                .font(.caption)
                                .foregroundColor(.watercolorSlate.opacity(0.52))
                                .lineLimit(1)
                        }
                    }

                    Text(suggestion.displayTitle)
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(.watercolorSlate)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)

                    if let rationale = suggestion.rationale, !rationale.isEmpty {
                        Text(rationale)
                            .font(.caption)
                            .foregroundColor(.watercolorSlate.opacity(0.66))
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                    }
                }

                Spacer(minLength: 0)

                selectionIndicator
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(cardSurface)
        }
        .buttonStyle(EditorialCardButtonStyle())
        .accessibilityIdentifier("onboarding.suggestion.\(suggestion.stableKey)")
    }

    private var selectionIndicator: some View {
        ZStack {
            Circle()
                .fill(
                    isSelected
                        ? Color.watercolorSlate.opacity(0.12)
                        : Color.watercolorSlate.opacity(0.06)
                )
                .frame(width: 30, height: 30)

            Image(systemName: isSelected ? "checkmark" : "plus")
                .font(.system(size: 12, weight: .bold))
                .foregroundColor(isSelected ? .watercolorSlate : .watercolorSlate.opacity(0.55))
        }
        .padding(.top, 2)
    }

    private var sourceDetail: String? {
        if suggestion.suggestionType == "reddit" {
            return nil
        }

        if let siteURL = suggestion.siteURL,
           let host = formattedHost(siteURL)
        {
            return host
        }

        if let feedURL = suggestion.feedURL,
           let host = formattedHost(feedURL)
        {
            return host
        }

        return nil
    }

    private var cardSurface: some View {
        RoundedRectangle(cornerRadius: 16)
            .fill(
                isSelected
                    ? Color.watercolorBase.opacity(0.94)
                    : Color.watercolorBase.opacity(0.86)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16)
                    .stroke(
                        isSelected
                            ? Color.watercolorSlate.opacity(0.14)
                            : Color.watercolorSlate.opacity(0.08),
                        lineWidth: 0.5
                    )
            )
            .shadow(color: .black.opacity(isSelected ? 0.07 : 0.04), radius: 14, x: 0, y: 10)
    }

    private func formattedHost(_ urlString: String) -> String? {
        guard let url = URL(string: urlString), let host = url.host else {
            return nil
        }
        return host.replacingOccurrences(of: "www.", with: "")
    }
}
