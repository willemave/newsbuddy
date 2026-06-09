//
//  InterleavedSummaryView.swift
//  newsly
//
//  Created by Assistant on 12/27/25.
//

import SwiftUI

// MARK: - Design Tokens
private enum InterleavedDesign {
    static let sectionSpacing: CGFloat = 24
    static let insightSpacing: CGFloat = 20
    static let quoteBarWidth: CGFloat = 3
}

struct InterleavedSummaryView: View {
    let summary: InterleavedSummary
    var contentId: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: InterleavedDesign.sectionSpacing) {
            // Hook
            Text(summary.hook)
                .font(.appCallout)
                .foregroundColor(Color.readerBodyText)
                .fixedSize(horizontal: false, vertical: true)

            // Insights
            VStack(alignment: .leading, spacing: InterleavedDesign.insightSpacing) {
                ForEach(summary.insights) { insight in
                    insightCard(insight)
                }
            }

            // Takeaway
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Image(systemName: "lightbulb")
                        .font(.readerBody)
                        .foregroundColor(.terracottaPrimary)
                        .accessibilityHidden(true)
                    Text("Takeaway")
                        .font(.readerBody)
                        .foregroundColor(Color.onSurfaceSecondary)
                }

                Text(summary.takeaway)
                    .font(.appCallout)
                    .foregroundColor(Color.readerBodyText)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - Insight Card
    @ViewBuilder
    private func insightCard(_ insight: InterleavedInsight) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(insight.topic)
                .font(.readerBody)
                .foregroundColor(Color.onSurface)

            // Insight text
            Text(insight.insight)
                .font(.appCallout)
                .foregroundColor(Color.readerBodyText)
                .fixedSize(horizontal: false, vertical: true)

            // Quote (if present)
            if let quote = insight.supportingQuote, !quote.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text(quote)
                        .font(.appCallout)
                        .italic()
                        .foregroundColor(Color.readerBodyText)
                        .fixedSize(horizontal: false, vertical: true)

                    if let attribution = insight.quoteAttribution, !attribution.isEmpty {
                        Text("— \(attribution)")
                            .font(.appFootnote)
                            .fontWeight(.medium)
                            .foregroundColor(Color.onSurfaceSecondary)
                    }
                }
                .padding(.leading, 14)
                .overlay(
                    Rectangle()
                        .fill(
                            LinearGradient(
                                colors: [Color.terracottaPrimary.opacity(0.8), Color.terracottaPrimary.opacity(0.4)],
                                startPoint: .top,
                                endPoint: .bottom
                            )
                        )
                        .frame(width: InterleavedDesign.quoteBarWidth),
                    alignment: .leading
                )
            }
        }
    }
}

#Preview {
    ScrollView {
        InterleavedSummaryView(
            summary: InterleavedSummary(
                summaryType: "interleaved",
                title: "The Future of AI Development",
                hook: "This article explores how large language models are transforming the way we build software, with implications for developers and organizations worldwide.",
                insights: [
                    InterleavedInsight(
                        topic: "Productivity Gains",
                        insight: "Developers using AI assistants report 40% faster completion times for routine coding tasks. This shift is changing how teams allocate their time and resources.",
                        supportingQuote: "We've seen our junior developers become productive much faster when they have AI pair programming tools available to guide them through unfamiliar codebases.",
                        quoteAttribution: "Engineering Lead at TechCorp"
                    ),
                    InterleavedInsight(
                        topic: "Quality Concerns",
                        insight: "While speed increases are notable, some teams report challenges with code maintainability when AI-generated code isn't carefully reviewed.",
                        supportingQuote: nil,
                        quoteAttribution: nil
                    ),
                    InterleavedInsight(
                        topic: "Adoption Patterns",
                        insight: "Adoption is highest among startups and mid-size companies, with enterprise organizations moving more cautiously due to security and compliance requirements.",
                        supportingQuote: "The productivity benefits are clear, but we need to ensure our IP and customer data remain protected before we can fully embrace these tools.",
                        quoteAttribution: "CTO of a Fortune 500 company"
                    )
                ],
                takeaway: "As AI coding tools mature, organizations that thoughtfully integrate them into their workflows will likely see significant competitive advantages, while those that delay may find themselves struggling to attract and retain developer talent.",
                classification: "to_read",
                summarizationDate: nil
            ),
            contentId: 123
        )
        .padding()
    }
}
