//
//  DetailSummarySections.swift
//  newsly
//

import SwiftUI

struct DetailSummarySections: View {
    let content: ContentDetail
    let startTopicSession: (String) async throws -> ChatSessionSummary
    let onSummaryAppear: (_ section: String, _ bulletPointCount: Int, _ insightCount: Int) -> Void

    @ViewBuilder
    var body: some View {
        if let longformArtifact = content.longformArtifact {
            summarySection(
                section: "longform_artifact",
                bulletPointCount: longformArtifact.artifact.payload.keyPoints.count,
                insightCount: 0
            ) {
                LongformArtifactView(artifact: longformArtifact, contentId: content.id)
            }
        } else if let editorialSummary = content.editorialSummary {
            summarySection(
                section: "editorial_v1",
                bulletPointCount: editorialSummary.keyPoints.count,
                insightCount: 0
            ) {
                EditorialNarrativeSummaryView(summary: editorialSummary, contentId: content.id)
            }
        } else if let bulletedSummary = content.bulletedSummary {
            summarySection(
                section: "bulleted_v1",
                bulletPointCount: bulletedSummary.points.count,
                insightCount: 0
            ) {
                BulletedSummaryView(summary: bulletedSummary, contentId: content.id)
            }
        } else if let interleavedSummary = content.interleavedSummaryV2 {
            summarySection(
                section: "interleaved_v2",
                bulletPointCount: interleavedSummary.keyPoints.count,
                insightCount: 0
            ) {
                InterleavedSummaryV2View(summary: interleavedSummary, contentId: content.id)
            }
        } else if let interleavedSummary = content.interleavedSummary {
            summarySection(
                section: "interleaved_v1",
                bulletPointCount: 0,
                insightCount: interleavedSummary.insights.count
            ) {
                InterleavedSummaryView(summary: interleavedSummary, contentId: content.id)
            }
        } else if let structuredSummary = content.structuredSummary {
            summarySection(
                section: "structured",
                bulletPointCount: structuredSummary.bulletPoints.count,
                insightCount: 0
            ) {
                StructuredSummaryView(
                    summary: structuredSummary,
                    contentId: content.id,
                    startTopicSession: startTopicSession
                )
            }
        }
    }

    private func summarySection<SummaryContent: View>(
        section: String,
        bulletPointCount: Int,
        insightCount: Int,
        @ViewBuilder content: () -> SummaryContent
    ) -> some View {
        content()
            .padding(.horizontal, DetailSummarySectionsDesign.horizontalPadding)
            .padding(.top, DetailSummarySectionsDesign.topPadding)
            .onAppear {
                onSummaryAppear(section, bulletPointCount, insightCount)
            }
    }
}

private enum DetailSummarySectionsDesign {
    static let horizontalPadding: CGFloat = Spacing.appHorizontalMargin
    static let topPadding: CGFloat = 14
}
