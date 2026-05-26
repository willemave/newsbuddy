//
//  LongformArtifactView.swift
//  newsly
//
//  Renderer for typed long-form artifacts.
//

import SwiftUI

private enum ArtifactDesign {
    static let sectionSpacing: CGFloat = 20
    static let rowSpacing: CGFloat = 10
}

struct LongformArtifactView: View {
    let artifact: LongformArtifactEnvelope
    var contentId: Int?

    private var accent: Color {
        artifact.detailAccent
    }

    var body: some View {
        ArtifactScaffold(artifact: artifact, accent: accent)
    }
}

private extension LongformArtifactEnvelope {
    var detailAccent: Color {
        switch artifact.type {
        case "argument":
            return .terracottaPrimary
        case "mental_model":
            return .summarySecondaryAccent
        case "playbook":
            return .summarySecondaryAccent
        case "portrait":
            return .summaryQuestionAccent
        case "briefing":
            return .terracottaDark
        case "walkthrough":
            return .summaryQuestionAccent
        case "findings":
            return .summaryCounterpointAccent
        default:
            return .terracottaPrimary
        }
    }
}

private struct ArtifactScaffold: View {
    let artifact: LongformArtifactEnvelope
    let accent: Color

    var body: some View {
        VStack(alignment: .leading, spacing: ArtifactDesign.sectionSpacing) {
            ForEach(artifact.detailSections) { section in
                ArtifactDetailSectionView(section: section, tint: accent)
            }
        }
    }
}

private struct ArtifactDetailSectionView: View {
    let section: LongformArtifactDetailSection
    let tint: Color

    var body: some View {
        switch section {
        case .takeaway(let text):
            TakeawayBanner(text: text, tint: tint)
        case .keyPoints(let points):
            KeyPointList(points: points, tint: tint)
        case .sourceQuotes(let quotes):
            SourceQuotesSection(quotes: quotes, tint: tint)
        case .extra(let sections):
            ExtraSection(sections: sections, tint: tint)
        }
    }
}

private struct ExtraSection: View {
    let sections: [LongformExtrasSection]
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ArtifactSectionHeader("Extra", icon: "square.stack.3d.up", tint: tint)

            VStack(alignment: .leading, spacing: 14) {
                ForEach(sections) { section in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(section.title)
                            .font(.footnote)
                            .fontWeight(.semibold)
                            .foregroundColor(Color.onSurfaceSecondary)
                            .textCase(.uppercase)
                            .tracking(0.5)

                        ForEach(section.items, id: \.self) { item in
                            ArtifactBulletRow(text: item)
                        }
                    }
                }
            }
        }
    }
}

private struct SourceQuotesSection: View {
    let quotes: [LongformArtifactQuote]
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ArtifactSectionHeader("Source Quotes", icon: "quote.opening", tint: tint)
            ForEach(quotes) { quote in
                ArtifactQuoteCard(quote: quote, tint: tint)
            }
        }
    }
}

private struct KeyPointList: View {
    let points: [LongformArtifactKeyPoint]
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ArtifactSectionHeader("Key Points", icon: "list.bullet.rectangle", tint: tint)

            VStack(alignment: .leading, spacing: 14) {
                ForEach(points) { point in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(point.heading)
                            .font(.callout)
                            .fontWeight(.semibold)
                            .foregroundColor(Color.onSurface)
                            .fixedSize(horizontal: false, vertical: true)

                        Text(point.content)
                            .font(.callout)
                            .foregroundColor(Color.onSurface.opacity(0.88))
                            .lineSpacing(3)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }
}

private struct TakeawayBanner: View {
    let text: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ArtifactSectionHeader("Takeaway", icon: "checkmark.seal", tint: tint)
            Text(text)
                .font(.callout)
                .fontWeight(.medium)
                .foregroundStyle(Color.onSurface)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct ArtifactQuoteCard: View {
    let quote: LongformArtifactQuote
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(quote.text)
                .font(.callout)
                .italic()
                .foregroundColor(Color.onSurface.opacity(0.9))
                .fixedSize(horizontal: false, vertical: true)

            if let attribution = quote.attribution?.trimmingCharacters(in: .whitespacesAndNewlines),
               !attribution.isEmpty {
                Text("- \(attribution)")
                    .font(.footnote)
                    .fontWeight(.medium)
                    .foregroundColor(Color.onSurfaceSecondary)
            }
        }
        .padding(.leading, 14)
        .overlay(
            Rectangle()
                .fill(tint.opacity(0.55))
                .frame(width: 3),
            alignment: .leading
        )
    }
}

private struct ArtifactBulletRow: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(Color.onSurface.opacity(0.5))
                .frame(width: 5, height: 5)
                .padding(.top, 7)
            Text(text)
                .font(.callout)
                .foregroundColor(Color.onSurface.opacity(0.9))
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct ArtifactSectionHeader: View {
    let title: String
    let icon: String
    let tint: Color

    init(_ title: String, icon: String, tint: Color) {
        self.title = title
        self.icon = icon
        self.tint = tint
    }

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .font(.subheadline)
                .foregroundColor(tint)
                .accessibilityHidden(true)
            Text(title)
                .font(.subheadline)
                .fontWeight(.semibold)
                .foregroundColor(Color.onSurfaceSecondary)
                .textCase(.uppercase)
                .tracking(0.5)
        }
    }
}
