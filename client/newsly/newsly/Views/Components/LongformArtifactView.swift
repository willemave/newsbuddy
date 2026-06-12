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
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private extension LongformArtifactEnvelope {
    // Single accent across all artifact types; section text stays neutral.
    var detailAccent: Color { .brandPrimary }
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
        .frame(maxWidth: .infinity, alignment: .leading)
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
                        ArtifactEyebrowText(section.title)

                        ForEach(section.items, id: \.self) { item in
                            Text(item)
                                .font(.appCallout)
                                .foregroundColor(Color.readerBodyText)
                                .lineSpacing(3)
                                .fixedSize(horizontal: false, vertical: true)
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
            ForEach(Array(quotes.enumerated()), id: \.offset) { _, quote in
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
                ForEach(Array(points.enumerated()), id: \.offset) { _, point in
                    VStack(alignment: .leading, spacing: 5) {
                        ArtifactEyebrowText(point.heading)

                        Text(point.content)
                            .font(.appCallout)
                            .foregroundColor(Color.readerBodyText)
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
                .font(.appCallout)
                .foregroundStyle(Color.readerBodyText)
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
                .font(.appSansItalic(size: 16, relativeTo: .callout))
                .foregroundColor(Color.readerBodyText)
                .fixedSize(horizontal: false, vertical: true)

            if let attribution = quote.attribution?.trimmingCharacters(in: .whitespacesAndNewlines),
               !attribution.isEmpty {
                Text("- \(attribution)")
                    .font(.appFootnote)
                    .fontWeight(.medium)
                    .foregroundColor(Color.onSurfaceSecondary)
            }
        }
        .padding(.leading, 14)
        .overlay(
            Rectangle()
                .fill(Color.onSurfaceTertiary.opacity(0.55))
                .frame(width: 3),
            alignment: .leading
        )
    }
}

private struct ArtifactEyebrowText: View {
    let title: String

    init(_ title: String) {
        self.title = title
    }

    var body: some View {
        Text(title.uppercased())
            .font(.readerBody)
            .foregroundColor(Color.onSurfaceSecondary)
            .tracking(0.4)
            .fixedSize(horizontal: false, vertical: true)
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
                .font(.readerBody)
                .foregroundColor(tint)
                .accessibilityHidden(true)
            Text(title.uppercased())
                .font(.readerBody)
                .foregroundColor(Color.onSurfaceSecondary)
                .tracking(0.4)
        }
    }
}
