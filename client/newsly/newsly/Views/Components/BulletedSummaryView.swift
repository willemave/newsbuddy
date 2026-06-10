//
//  BulletedSummaryView.swift
//  newsly
//
//  Bullet-first summary with always-visible details and quotes.
//

import SwiftUI

private enum BulletedSummaryDesign {
    static let sectionSpacing: CGFloat = 18
    static let itemSpacing: CGFloat = 10
    static let quoteBarWidth: CGFloat = 3
}

struct BulletedSummaryView: View {
    let summary: BulletedSummary
    var contentId: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: BulletedSummaryDesign.sectionSpacing) {
            VStack(alignment: .leading, spacing: BulletedSummaryDesign.itemSpacing) {
                ForEach(summary.points) { point in
                    bulletPointRow(point: point)
                }
            }
        }
    }

    @ViewBuilder
    private func bulletPointRow(point: BulletSummaryPoint) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Circle()
                    .fill(Color.onSurface.opacity(0.5))
                    .frame(width: 5, height: 5)
                    .padding(.top, 8)
                    .accessibilityHidden(true)

                Text(point.text)
                    .font(.appCallout)
                    .foregroundColor(Color.readerBodyText)
                    .multilineTextAlignment(.leading)

                Spacer()
            }

            VStack(alignment: .leading, spacing: 10) {
                Text(point.detail)
                    .font(.appSubheadline)
                    .foregroundColor(Color.readerBodyText)
                    .fixedSize(horizontal: false, vertical: true)

                if !point.quotes.isEmpty {
                    VStack(alignment: .leading, spacing: 12) {
                        ForEach(Array(point.quotes.enumerated()), id: \.offset) { _, quote in
                            quoteCard(quote)
                        }
                    }
                }
            }
            .padding(.leading, 16)
        }
        .padding(.vertical, 6)
    }

    @ViewBuilder
    private func quoteCard(_ quote: Quote) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(quote.text)
                .font(.appSansItalic(size: 15, relativeTo: .subheadline))
                .foregroundColor(Color.readerBodyText)
                .fixedSize(horizontal: false, vertical: true)

            if let attributionLine = quoteAttributionLine(quote) {
                Text("— \(attributionLine)")
                    .font(.appFootnote)
                    .fontWeight(.medium)
                    .foregroundColor(Color.onSurfaceSecondary)
            }
        }
        .padding(.leading, 12)
        .overlay(
            Rectangle()
                .fill(
                    LinearGradient(
                        colors: [Color.terracottaPrimary.opacity(0.8), Color.terracottaPrimary.opacity(0.4)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: BulletedSummaryDesign.quoteBarWidth),
            alignment: .leading
        )
    }

    private func quoteAttributionLine(_ quote: Quote) -> String? {
        let candidates: [String?] = [quote.attribution, quote.context]
        let parts: [String] = candidates.compactMap { value in
            guard let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !trimmed.isEmpty else { return nil }
            return trimmed
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

#Preview {
    ScrollView {
        BulletedSummaryView(
            summary: BulletedSummary(
                title: "Bulleted Summary",
                points: [
                    BulletSummaryPoint(
                        text: "Enterprise teams are consolidating agent workflows across departments.",
                        detail: "Procurement and security teams are pushing for fewer vendors and clearer controls. This consolidation is accelerating standardization of internal agent tooling.",
                        quotes: [
                            Quote(
                                text: "We can't have five different agent stacks in one company.",
                                context: "Security lead",
                                attribution: nil
                            )
                        ]
                    ),
                    BulletSummaryPoint(
                        text: "Cost visibility is becoming a primary driver of agent adoption choices.",
                        detail: "Teams are demanding per-task cost reporting to justify ongoing spend. Vendor selection is increasingly driven by predictability rather than raw capability.",
                        quotes: [
                            Quote(
                                text: "If we can't predict the bill, we can't roll it out.",
                                context: "Finance stakeholder",
                                attribution: nil
                            )
                        ]
                    )
                ],
                classification: "to_read",
                summarizationDate: "2026-02-04T12:00:00Z"
            )
        )
        .padding()
    }
}
