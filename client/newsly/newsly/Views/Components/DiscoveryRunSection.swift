//
//  DiscoveryRunSection.swift
//  newsly
//

import SwiftUI

struct DiscoveryRunSection: View {
    let run: DiscoveryRunSuggestions
    let isLatest: Bool
    let onSelect: (DiscoverySuggestion) -> Void

    private static let briefingDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d, yyyy"
        return formatter
    }()

    private static let iso8601WithFractionalFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let iso8601Formatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if isLatest {
                editorialBriefingHero
            } else {
                dateSeparator
            }

            // Flat mixed list of all suggestions
            VStack(spacing: 10) {
                ForEach(allSuggestions) { suggestion in
                    DiscoverySuggestionCard(
                        suggestion: suggestion,
                        suggestionType: suggestion.suggestionType,
                        onTap: { onSelect(suggestion) }
                    )
                }
            }
            .padding(.horizontal, Spacing.screenHorizontal)
        }
    }

    // MARK: - Editorial Briefing Hero

    private var editorialBriefingHero: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Uppercase tracking label
            Text(briefingLabel.uppercased())
                .font(.feedMeta)
                .foregroundColor(Color.onSurfaceSecondary)
                .tracking(0.8)
                .padding(.horizontal, Spacing.screenHorizontal)
                .padding(.top, Spacing.sectionTop)

            // Summary headline
            if let summary = run.directionSummary, !summary.isEmpty {
                let parts = splitSummary(summary)

                Text(parts.headline)
                    .font(.cardHeadline)
                    .foregroundColor(Color.onSurface)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, Spacing.screenHorizontal)
                    .padding(.top, 10)

                if let body = parts.body {
                    Text(body)
                        .font(.appSubheadline)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, Spacing.screenHorizontal)
                        .padding(.top, 8)
                }
            }

            // Spacer before cards
            Spacer().frame(height: 20)
        }
    }

    // MARK: - Date Separator (non-latest runs)

    private var dateSeparator: some View {
        HStack(spacing: 8) {
            Rectangle()
                .fill(Color.editorialBorder)
                .frame(height: 1)

            Text(runDateLabel.uppercased())
                .font(.editorialMeta)
                .foregroundColor(.editorialSub)
                .tracking(0.8)
                .fixedSize()

            Rectangle()
                .fill(Color.editorialBorder)
                .frame(height: 1)
        }
        .padding(.horizontal, Spacing.screenHorizontal)
        .padding(.top, 28)
        .padding(.bottom, 16)
    }

    // MARK: - Helpers

    private var allSuggestions: [DiscoverySuggestion] {
        run.feeds + run.podcasts + run.youtube
    }

    private var briefingLabel: String {
        let dateStr = formatBriefingDate(run.runCreatedAt)
        return "Daily Briefing \u{00B7} \(dateStr)"
    }

    private var runDateLabel: String {
        formatBriefingDate(run.runCreatedAt)
    }

    private func formatBriefingDate(_ dateString: String) -> String {
        guard let date = parseDate(dateString) else { return "Recent" }
        return Self.briefingDateFormatter.string(from: date)
    }

    private func splitSummary(_ text: String) -> (headline: String, body: String?) {
        // Split at first sentence boundary
        let sentenceEnders: [Character] = [".", "!", "?"]
        if let idx = text.firstIndex(where: { sentenceEnders.contains($0) }) {
            let headlineEnd = text.index(after: idx)
            let headline = String(text[text.startIndex..<headlineEnd]).trimmingCharacters(in: .whitespaces)
            let remaining = String(text[headlineEnd...]).trimmingCharacters(in: .whitespaces)
            return (headline, remaining.isEmpty ? nil : remaining)
        }
        return (text, nil)
    }

    private func parseDate(_ dateString: String) -> Date? {
        if let date = Self.iso8601WithFractionalFormatter.date(from: dateString) {
            return date
        }
        return Self.iso8601Formatter.date(from: dateString)
    }
}
