//
//  EditorialNarrativeSummaryView.swift
//  newsly
//
//  Narrative-first longform summary renderer.
//

import SwiftUI

private enum EditorialNarrativeDesign {
    static let sectionSpacing: CGFloat = 20
    static let itemSpacing: CGFloat = 10
    static let quoteBarWidth: CGFloat = 3
}

struct EditorialNarrativeSummaryView: View {
    let summary: EditorialNarrativeSummary
    var contentId: Int?
    @State private var expandedArchetypes: Set<String> = []

    var body: some View {
        VStack(alignment: .leading, spacing: EditorialNarrativeDesign.sectionSpacing) {
            VStack(alignment: .leading, spacing: 16) {
                ForEach(Array(summary.narrativeParagraphs.enumerated()), id: \.offset) { _, paragraph in
                    Text(paragraph)
                        .font(.callout)
                        .foregroundColor(.primary.opacity(0.92))
                        .lineSpacing(5)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if let archetypeReactions = summary.archetypeReactions, !archetypeReactions.isEmpty {
                sectionHeader("Perspectives", icon: "person.3.sequence", tint: .orange)
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(archetypeReactions) { reaction in
                        collapsibleArchetypeReaction(reaction)
                    }
                }
            }

            if !summary.keyPoints.isEmpty {
                sectionHeader("Key Points", icon: "list.bullet.rectangle", tint: .blue)
                VStack(alignment: .leading, spacing: EditorialNarrativeDesign.itemSpacing) {
                    ForEach(summary.keyPoints) { point in
                        bulletRow(text: point.point)
                    }
                }
            }

            if !summary.quotes.isEmpty {
                sectionHeader("Notable Quotes", icon: "quote.opening", tint: .purple)
                VStack(alignment: .leading, spacing: 16) {
                    ForEach(summary.quotes, id: \.text) { quote in
                        quoteCard(quote)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func collapsibleArchetypeReaction(_ reaction: EditorialArchetypeReaction) -> some View {
        let isExpanded = expandedArchetypes.contains(reaction.id)

        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    if isExpanded {
                        expandedArchetypes.remove(reaction.id)
                    } else {
                        expandedArchetypes.insert(reaction.id)
                    }
                }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "chevron.right")
                        .font(.caption2)
                        .fontWeight(.bold)
                        .foregroundColor(.secondary.opacity(0.7))
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))

                    Text(reaction.archetype)
                        .font(.callout)
                        .fontWeight(.semibold)
                        .foregroundColor(.primary)

                    Spacer()
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isExpanded {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(Array(reaction.displayParagraphs.enumerated()), id: \.offset) { _, paragraph in
                        Text(paragraph)
                            .font(.callout)
                            .foregroundColor(.primary.opacity(0.9))
                            .lineSpacing(4)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.top, 12)
                .padding(.leading, 22)
            }
        }
    }

    @ViewBuilder
    private func sectionHeader(
        _ title: String,
        icon: String,
        tint: Color,
        uppercase: Bool = true
    ) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .font(.subheadline)
                .foregroundColor(tint)
            Text(title)
                .font(.subheadline)
                .fontWeight(.semibold)
                .foregroundColor(.secondary)
                .textCase(uppercase ? .uppercase : .none)
                .tracking(uppercase ? 0.5 : 0)
        }
    }

    @ViewBuilder
    private func bulletRow(text: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(Color.primary.opacity(0.5))
                .frame(width: 5, height: 5)
                .padding(.top, 7)
            Text(text)
                .font(.callout)
                .foregroundColor(.primary.opacity(0.9))
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private func quoteCard(_ quote: Quote) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(quote.text)
                .font(.callout)
                .italic()
                .foregroundColor(.primary.opacity(0.9))
                .fixedSize(horizontal: false, vertical: true)

            if let attributionLine = quoteAttributionLine(quote) {
                Text("— \(attributionLine)")
                    .font(.footnote)
                    .fontWeight(.medium)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.leading, 14)
        .padding(.vertical, 2)
        .overlay(
            Rectangle()
                .fill(
                    LinearGradient(
                        colors: [.purple.opacity(0.8), .purple.opacity(0.4)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: EditorialNarrativeDesign.quoteBarWidth),
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
        EditorialNarrativeSummaryView(
            summary: EditorialNarrativeSummary(
                title: "AI Moves from Pilot to Operating Model",
                editorialNarrative: """
                Teams are no longer treating AI as a side project. The article argues that the operational center of gravity has moved into procurement, governance, and reliability engineering, where model usage is budgeted and audited like any other production dependency.

                It highlights a shift from one-off demos to system-level workflows, with leaders prioritizing measurable throughput and predictable failure handling over raw benchmark wins.
                """,
                quotes: [
                    Quote(
                        text: "We can’t scale this without clear ownership of model behavior in production.",
                        context: nil,
                        attribution: "Platform lead"
                    ),
                    Quote(
                        text: "The biggest upgrade was process discipline, not model size.",
                        context: nil,
                        attribution: "Engineering manager"
                    ),
                ],
                archetypeReactions: [
                    EditorialArchetypeReaction(
                        archetype: "Paul Graham",
                        paragraphs: [
                            "The interesting opportunity is not generic enterprise AI but the specific workflow friction teams feel before they have the language to describe it. Small teams can win here by turning governance pain into product taste.",
                            "What matters is whether users are pulled hard enough to change behavior. If they are, there is room to build tighter workflow software around the pain incumbents still treat as a feature request."
                        ]
                    ),
                    EditorialArchetypeReaction(
                        archetype: "Andy Grove",
                        paragraphs: [
                            "This looks like an operating shift more than a tooling shift. Governance moving upstream means the company is hitting a strategic inflection point where controls become part of product delivery.",
                            "The chokepoints are approval latency, vendor sprawl, and weak observability. Leaders should track those closely because they determine whether the organization scales or stalls."
                        ]
                    ),
                    EditorialArchetypeReaction(
                        archetype: "Charlie Munger",
                        paragraphs: [
                            "The deeper force is incentives: budget owners and security teams are now rewarded for reliability and transparency instead of demo quality. Once those incentives change, behavior follows.",
                            "That creates second-order effects in vendor selection, process discipline, and durable moats. The market usually notices the model upgrade before it notices the control structure around it."
                        ]
                    )
                ],
                keyPoints: [
                    EditorialKeyPoint(point: "Budget planning now includes model spend at workflow granularity."),
                    EditorialKeyPoint(point: "Evaluation gates are becoming mandatory before broad internal rollout."),
                    EditorialKeyPoint(point: "Reliability and observability requirements are narrowing model/vendor choices."),
                    EditorialKeyPoint(point: "Teams with tighter operational controls are shipping faster despite stricter review."),
                ],
                classification: "to_read",
                summarizationDate: "2026-02-08T12:00:00Z"
            )
        )
        .padding()
    }
}
