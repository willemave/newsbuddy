//
//  InterleavedSummaryV2View.swift
//  newsly
//
//  Created by Assistant on 1/20/26.
//

import SwiftUI

private enum InterleavedV2Design {
    static let sectionSpacing: CGFloat = 20
    static let itemSpacing: CGFloat = 10
    static let quoteBarWidth: CGFloat = 3
}

struct InterleavedSummaryV2View: View {
    let summary: InterleavedSummaryV2
    var contentId: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: InterleavedV2Design.sectionSpacing) {
            Text(summary.hook)
                .font(.appCallout)
                .foregroundColor(Color.readerBodyText)
                .fixedSize(horizontal: false, vertical: true)

            if !summary.keyPoints.isEmpty {
                ReaderSectionHeader("Key Points")
                VStack(alignment: .leading, spacing: InterleavedV2Design.itemSpacing) {
                    ForEach(Array(summary.keyPoints.enumerated()), id: \.offset) { _, point in
                        bulletRow(text: point.text)
                    }
                }
            }

            if !summary.quotes.isEmpty {
                ReaderSectionHeader("Notable Quotes")
                VStack(alignment: .leading, spacing: 16) {
                    ForEach(Array(summary.quotes.enumerated()), id: \.offset) { _, quote in
                        quoteCard(quote)
                    }
                }
            }

            if !summary.topics.isEmpty {
                ReaderSectionHeader("Topics")
                VStack(alignment: .leading, spacing: 16) {
                    ForEach(summary.topics) { topic in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(topic.topic.uppercased())
                                .font(.readerBody.weight(.bold))
                                .foregroundColor(Color.readerBodyText)
                                .tracking(0.4)

                            VStack(alignment: .leading, spacing: InterleavedV2Design.itemSpacing) {
                                ForEach(Array(topic.bullets.enumerated()), id: \.offset) { _, bullet in
                                    bulletRow(text: bullet.text)
                                }
                            }
                        }
                    }
                }
            }

            VStack(alignment: .leading, spacing: 8) {
                ReaderSectionHeader("Takeaway")
                Text(summary.takeaway)
                    .font(.appCallout)
                    .foregroundColor(Color.readerBodyText)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder
    private func bulletRow(text: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(Color.onSurface.opacity(0.5))
                .frame(width: 5, height: 5)
                .padding(.top, 7)
                .accessibilityHidden(true)
            Text(text)
                .font(.appCallout)
                .foregroundColor(Color.readerBodyText)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private func quoteCard(_ quote: Quote) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(quote.text)
                .font(.appSansItalic(size: 16, relativeTo: .callout))
                .foregroundColor(Color.readerBodyText)
                .fixedSize(horizontal: false, vertical: true)

            if let attributionLine = quoteAttributionLine(quote) {
                Text("— \(attributionLine)")
                    .font(.appFootnote)
                    .fontWeight(.medium)
                    .foregroundColor(Color.onSurfaceSecondary)
            }
        }
        .padding(.leading, 14)
        .padding(.vertical, 2)
        .overlay(
            Rectangle()
                .fill(
                    LinearGradient(
                        colors: [Color.onSurfaceTertiary.opacity(0.8), Color.onSurfaceTertiary.opacity(0.4)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: InterleavedV2Design.quoteBarWidth),
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
        InterleavedSummaryV2View(
            summary: InterleavedSummaryV2(
                title: "Interleaved Summary v2",
                hook: "This article explains how modern AI models are reshaping product development and why the next year will feel like a step-change in how teams ship software.",
                keyPoints: [
                    BulletPoint(text: "Model accuracy improved ~40% on standard benchmarks.", category: nil),
                    BulletPoint(text: "Training cost dropped by roughly half.", category: nil),
                    BulletPoint(text: "Tooling shifts are compressing launch cycles to days.", category: nil)
                ],
                topics: [
                    InterleavedTopic(
                        topic: "Performance Gains",
                        bullets: [
                            BulletPoint(text: "Benchmark improvements are consistent across tasks.", category: nil),
                            BulletPoint(text: "Compute efficiency allows broader deployment.", category: nil)
                        ]
                    ),
                    InterleavedTopic(
                        topic: "Operational Impact",
                        bullets: [
                            BulletPoint(text: "Teams can iterate on product flows much faster.", category: nil),
                            BulletPoint(text: "Quality gates shift toward data pipelines.", category: nil)
                        ]
                    )
                ],
                quotes: [
                    Quote(
                        text: "We were surprised by the magnitude of the improvements, which exceeded our initial expectations significantly.",
                        context: "Lab interview",
                        attribution: "Lead Researcher"
                    )
                ],
                takeaway: "The biggest winners will be teams that treat AI as an operational capability, not just a feature, and invest in data and feedback loops early.",
                classification: "to_read",
                summarizationDate: nil
            ),
            contentId: 123
        )
        .padding()
    }
}
