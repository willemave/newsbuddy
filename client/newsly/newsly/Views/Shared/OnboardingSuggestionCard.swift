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

    private static let tilePalette: [Color] = [
        .onboardingAmbientPrimary,
        .onboardingAmbientTertiary,
        .onboardingAmbientQuaternary,
    ]

    var body: some View {
        Button(action: onToggle) {
            HStack(alignment: .center, spacing: 12) {
                HStack(alignment: .center, spacing: 12) {
                    monogramTile

                    VStack(alignment: .leading, spacing: 2) {
                        Text(suggestion.displayTitle)
                            .font(.appSubheadline.weight(.semibold))
                            .foregroundColor(.onboardingText)
                            .lineLimit(1)

                        if let detail = secondaryDetail {
                            Text(detail)
                                .font(.appCaption)
                                .foregroundColor(.onboardingText.opacity(0.55))
                                .lineLimit(1)
                        }
                    }
                }
                .opacity(isSelected ? 1.0 : 0.55)

                Spacer(minLength: 0)

                OnboardingSelectionDot(isSelected: isSelected)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(EditorialCardButtonStyle())
        .animation(AppMotion.press, value: isSelected)
        .accessibilityIdentifier("onboarding.suggestion.\(suggestion.stableKey)")
    }

    private var monogramTile: some View {
        RoundedRectangle(cornerRadius: 10, style: .continuous)
            .fill(tileColor)
            .frame(width: 34, height: 34)
            .overlay(
                Text(monogram)
                    .font(.appSubheadline.weight(.bold))
                    .foregroundColor(.onboardingText)
            )
    }

    private var monogram: String {
        var title = suggestion.displayTitle
        if title.lowercased().hasPrefix("r/") {
            title = String(title.dropFirst(2))
        }
        guard let letter = title.first(where: { $0.isLetter || $0.isNumber }) else {
            return "·"
        }
        return String(letter).uppercased()
    }

    // Deterministic tint so a source keeps its color across renders and launches
    // (Hasher is seed-randomized per launch, so use a plain djb2 over the key).
    private var tileColor: Color {
        var hash: UInt64 = 5381
        for byte in suggestion.stableKey.utf8 {
            hash = hash &* 33 &+ UInt64(byte)
        }
        return Self.tilePalette[Int(hash % UInt64(Self.tilePalette.count))]
    }

    private var secondaryDetail: String? {
        if let host = sourceDetail, !host.isEmpty {
            return host
        }
        if let rationale = suggestion.rationale, !rationale.isEmpty {
            return rationale
        }
        return nil
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

    private func formattedHost(_ urlString: String) -> String? {
        guard let url = URL(string: urlString), let host = url.host else {
            return nil
        }
        return host.replacingOccurrences(of: "www.", with: "")
    }
}
