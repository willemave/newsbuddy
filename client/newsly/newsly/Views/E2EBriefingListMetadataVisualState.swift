#if DEBUG
import SwiftUI

/// Exercises the real list renderer with both close and divider-sized time gaps.
struct E2EBriefingListMetadataVisualState: View {
    @State private var tier: APIBriefingTier = .longform
    @State private var chromeCollapse = BriefingChromeCollapseModel()

    private var title: String {
        tier == .audio ? "Podcasts" : "Articles"
    }

    var body: some View {
        VStack(spacing: 0) {
            Picker("List", selection: $tier) {
                Text("Articles").tag(APIBriefingTier.longform)
                Text("Podcasts").tag(APIBriefingTier.audio)
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, Spacing.appHorizontalMargin)

            BriefingLensPageView(
                lensKey: title,
                lensTitle: title,
                renderModel: BriefingLensRenderModel(lens: makeLens()),
                isReadTrackingEnabled: false,
                readBoundaryY: nil,
                documentGeneration: 1,
                scrollToTopRequest: 0,
                shouldScrollToTop: false,
                error: nil,
                continuationError: nil,
                isLoadingContinuation: false,
                chromeCollapse: chromeCollapse,
                collapsibleChromeHeight: 0,
                topContentInset: 20,
                onOpenSource: { _ in },
                onOpenDiscussion: { _ in },
                onDig: { _, _ in },
                onRefresh: {},
                onLoad: {},
                onRetry: {},
                onFirstPassageVisible: {},
                onScrolledDown: {},
                onMarkSegmentSeen: { _ in },
                onSetHeaderPinned: { _ in }
            )
            .id(tier)
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
    }

    private func makeLens() -> APIBriefingLensResponse {
        let dates = ["2026-10-02T14:32:00Z", "2026-10-02T14:31:00Z", "2026-10-01T14:32:00Z"]
        let publishers: [String?] = ["Latent Space", "A Very Long Publication Name", nil]
        let sources = dates.indices.map { index in
            APIBriefingSource(
                sourceKey: "content:\(index + 1)", kind: "content", id: index + 1,
                title: tier == .audio
                    ? "Inside-Out AI: A conversation about rebuilding the guest experience"
                    : "Inside-Out AI: Rebuilding Airbnb Behind the Scenes and Across the Guest Experience",
                publisher: publishers[index], aggregator: nil, summary: nil, keyPoints: nil, url: nil,
                imageUrl: nil, thumbnailUrl: nil, publishedAt: index == 0 ? ServerDate.parse("2023-04-01T12:00:00Z") : nil,
                contentType: tier == .audio ? .podcast : .article,
                durationSeconds: tier == .audio ? 3_939 : nil,
                readingMinutes: tier == .audio ? nil : 9, discussion: nil
            )
        }
        let segments = dates.enumerated().map { index, rawDate in
            APIBriefingSegment(
                id: index + 1,
                createdAt: ServerDate.parse(rawDate)!,
                status: "active",
                narrationText: "",
                blocks: [APIBriefingBlock(
                    type: .passage, weight: nil,
                    paragraphs: [APIBriefingParagraph(runs: [APIBriefingRun(
                        kind: .text,
                        text: "Airbnb’s inside-out AI operating model starts by changing how teams build. Product, design, and engineering work directly with code and prototypes, aiming to cut handoffs and test ideas sooner.",
                        sourceKey: nil, insightId: nil
                    )])],
                    sourceKey: nil, imageUrl: nil, thumbnailUrl: nil,
                    caption: nil, placement: nil, alignment: nil, text: nil
                )],
                sourceKeys: [sources[index].sourceKey]
            )
        }
        return APIBriefingLensResponse(
            version: 1,
            lens: APIBriefingLensSummary(
                key: title, tier: tier, title: title, deck: "", position: 0,
                segmentCount: segments.count, unreadSourceCount: sources.count
            ),
            segments: segments, sources: sources, nextCursor: nil
        )
    }
}
#endif
