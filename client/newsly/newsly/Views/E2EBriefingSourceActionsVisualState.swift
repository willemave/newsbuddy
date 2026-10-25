#if DEBUG
import SwiftUI

/// Backend-free evidence of the production renderer's link gestures and source identity.
struct E2EBriefingSourceActionsVisualState: View {
    @State private var tier: APIBriefingTier = .news
    @State private var chromeCollapse = BriefingChromeCollapseModel()
    @State private var result = "No action"

    private var sourceKey: String { tier == .news ? "news:42" : "content:42" }
    private var title: String {
        switch tier {
        case .news: "Linked news"
        case .audio: "Linked podcast"
        default: "Linked article"
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            Picker("Briefing", selection: $tier) {
                Text("News").tag(APIBriefingTier.news)
                Text("Articles").tag(APIBriefingTier.longform)
                Text("Podcasts").tag(APIBriefingTier.audio)
            }
            .pickerStyle(.segmented)
            .padding()

            Text(result)
                .accessibilityIdentifier("briefing.source_actions.result")

            BriefingLensPageView(
                lensKey: "fixture", lensTitle: "Briefing",
                renderModel: BriefingLensRenderModel(lens: lens),
                isReadTrackingEnabled: false, readBoundaryY: nil,
                documentGeneration: 1, scrollToTopRequest: 0, shouldScrollToTop: false,
                error: nil, continuationError: nil, isLoadingContinuation: false,
                chromeCollapse: chromeCollapse, collapsibleChromeHeight: 0, topContentInset: 20,
                onOpenSource: { result = "Open \($0)" },
                onSourceAction: { key, action in result = "\(action.title): \(key)" },
                onOpenDiscussion: { result = "Discussion \($0.sourceKey)" },
                onDig: { _, _ in result = "Dig Deeper" },
                onRefresh: {}, onLoad: {}, onRetry: {}, onFirstPassageVisible: {},
                onScrolledDown: {}, onMarkSegmentSeen: { _ in }, onSetHeaderPinned: { _ in }
            )
            .id(tier)
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        .onAppear { UIView.setAnimationsEnabled(false) }
    }

    private var lens: APIBriefingLensResponse {
        let source = APIBriefingSource(
            sourceKey: sourceKey, kind: tier == .news ? "news" : "content", id: 42,
            title: title, publisher: "Newsly", aggregator: tier == .news ? "hackernews" : nil,
            summary: nil, keyPoints: nil, url: nil,
            imageUrl: nil, thumbnailUrl: nil, publishedAt: nil,
            contentType: tier == .news ? .news : (tier == .audio ? .podcast : .article),
            durationSeconds: nil, readingMinutes: nil,
            discussion: tier == .news ? APIBriefingDiscussion(
                platform: "hackernews", commentCount: 128, summaryStatus: "not_ready",
                overview: nil, topCommentAuthor: nil, topCommentText: nil,
                externalUrl: nil, updatedAt: nil
            ) : nil
        )
        let segment = APIBriefingSegment(
            id: 42, createdAt: Date(), status: "active", narrationText: "",
            blocks: [APIBriefingBlock(
                type: .passage, weight: nil,
                paragraphs: [APIBriefingParagraph(runs: [
                    APIBriefingRun(kind: .source_link, text: title, sourceKey: sourceKey, insightId: nil),
                    APIBriefingRun(kind: .text, text: " explains how a new discovery changes our understanding. Select these words to dig deeper, or hold the link to save and explore the source.", sourceKey: nil, insightId: nil)
                ])],
                sourceKey: nil, imageUrl: nil, thumbnailUrl: nil, caption: nil,
                placement: nil, alignment: nil, text: nil
            )],
            sourceKeys: [sourceKey]
        )
        return APIBriefingLensResponse(
            version: 1,
            lens: APIBriefingLensSummary(
                key: "fixture", tier: tier, title: "Briefing", deck: "", position: 0,
                segmentCount: 1, unreadSourceCount: 1
            ),
            segments: [segment], sources: [source], nextCursor: nil
        )
    }
}
#endif
