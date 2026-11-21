#if DEBUG
import SwiftUI

/// Exercises the real Articles and Podcasts card renderer: publisher taglines,
/// a three-line title, unknown length, an older publication date, and a read card.
struct E2EBriefingListMetadataVisualState: View {
    @State private var tier: APIBriefingTier = .longform
    @State private var chromeCollapse = BriefingChromeCollapseModel()
    @State private var savedSources = BriefingSavedSources(saveSource: { _ in "Saved to Knowledge" })

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
                onSourceAction: { sourceKey, action in
                    guard action == .addToKnowledge else { return }
                    Task { _ = await savedSources.save(sourceKey: sourceKey, source: .content(1)) }
                },
                savedSources: savedSources,
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

    private struct Fixture {
        let title: String
        let publisher: String?
        let publishedAt: String?
        let length: Int?
        let read: Bool
        let summary: String
    }

    private var fixtures: [Fixture] {
        if tier == .audio {
            return [
                Fixture(
                    title: "The (Not So) Great Wealth Transfer",
                    publisher: "Moody's Talks - Inside Economics", publishedAt: "2026-09-25T12:00:00Z",
                    length: 3_939, read: false,
                    summary: "Of the $93 trillion headed to heirs, Moody's economists estimate only about $8 trillion will be spent once liabilities, retirement needs, and taxes are counted. Mark Zandi argues the inheritance boom will barely move consumer spending, because most transferred wealth is saved or spent down by the givers first."
                ),
                Fixture(
                    title: "Inside-Out AI: A conversation about rebuilding the guest experience from the ground up",
                    publisher: "Venturing with Vishesh — Startup Founder Interviews", publishedAt: nil,
                    length: nil, read: false,
                    summary: "Airbnb's inside-out AI operating model starts by changing how teams build. Product, design, and engineering work directly with prototypes to cut handoffs and test ideas sooner."
                ),
                Fixture(
                    title: "The Life and, for Now, the Death of the Kennedy Center",
                    publisher: "The Daily", publishedAt: "2026-09-25T12:00:00Z",
                    length: 1_824, read: true,
                    summary: "A board takeover, staff firings, and plunging ticket sales ended in a closure justified by a facilities dispute."
                ),
            ]
        }
        return [
            Fixture(
                title: "The Harness-Maxxing Trap",
                publisher: "LLM Watch", publishedAt: "2026-08-25T12:00:00Z",
                length: 10, read: false,
                summary: "A [benchmark score belongs to a model and harness pair](newsly://briefing/content/1), not the model alone. LangChain lifted its coding agent from 52.8% to 66.5% on Terminal-Bench 2.0 without touching the model, so deployment rules should stay outside the optimization loop."
            ),
            Fixture(
                title: "Inside-Out AI: Rebuilding Airbnb Behind the Scenes and Across the Guest Experience",
                publisher: "A Very Long Publication Name | Network", publishedAt: "2023-04-01T12:00:00Z",
                length: nil, read: false,
                summary: "Airbnb's inside-out AI operating model starts by changing how teams build. Product, design, and engineering work directly with code and prototypes, aiming to cut handoffs and test ideas sooner."
            ),
            Fixture(
                title: "Nuclear Energy, Part 3: Are SMRs the Bet Worth Making?",
                publisher: "The Climate Tech VC Journey", publishedAt: "2026-04-09T12:00:00Z",
                length: 8, read: true,
                summary: "Small modular reactors could make nuclear cheaper and faster to build, but the case still hinges on cost and regulation."
            ),
        ]
    }

    private func makeLens() -> APIBriefingLensResponse {
        let fixtures = fixtures
        let sources = fixtures.enumerated().map { index, fixture in
            APIBriefingSource(
                sourceKey: "content:\(index + 1)", kind: "content", id: index + 1,
                title: fixture.title,
                publisher: fixture.publisher, aggregator: nil, summary: nil, keyPoints: nil, url: nil,
                imageUrl: nil, thumbnailUrl: nil,
                publishedAt: fixture.publishedAt.flatMap(ServerDate.parse),
                contentType: tier == .audio ? .podcast : .article,
                durationSeconds: tier == .audio ? fixture.length : nil,
                readingMinutes: tier == .audio ? nil : fixture.length,
                read: fixture.read,
                discussion: nil
            )
        }
        let segments = fixtures.enumerated().map { index, fixture in
            APIBriefingSegment(
                id: index + 1,
                createdAt: ServerDate.parse("2026-10-02T14:32:00Z")!,
                status: "active",
                narrationText: "",
                blocks: [Self.passage(fixture.summary)],
                sourceKeys: [sources[index].sourceKey]
            )
        }
        return APIBriefingLensResponse(
            version: 1,
            lens: APIBriefingLensSummary(
                key: title, tier: tier, title: title, deck: "", position: 0,
                segmentCount: segments.count,
                unreadSourceCount: fixtures.filter { !$0.read }.count
            ),
            segments: segments, sources: sources, nextCursor: nil
        )
    }

    private static func passage(_ text: String) -> APIBriefingBlock {
        APIBriefingBlock(
            type: .passage, weight: nil,
            paragraphs: [APIBriefingParagraph(runs: [APIBriefingRun(
                kind: .text, text: text, sourceKey: nil, insightId: nil
            )])],
            sourceKey: nil, imageUrl: nil, thumbnailUrl: nil,
            caption: nil, placement: nil, alignment: nil, text: nil
        )
    }
}

/// Frozen mid-swipe states over the large background actions: Chat selected while
/// dragging right, and armed mark-read.
struct E2EBriefingCardSwipeVisualState: View {
    var body: some View {
        VStack(spacing: 24) {
            frozen(dx: 90, dy: -40, anchorY: 220)
            frozen(dx: -110, dy: 0, anchorY: 220)
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .frame(maxHeight: .infinity)
        .background(Color.surfacePrimary.ignoresSafeArea())
    }

    private func frozen(dx: CGFloat, dy: CGFloat, anchorY: CGFloat) -> some View {
        let swipe = BriefingCardSwipe(translation: CGSize(width: dx, height: dy))
        return card
            .opacity(swipe.cardOpacity)
            .scaleEffect(swipe.cardScale)
            .offset(x: swipe.cardOffset)
            .background {
                BriefingCardSwipeBackdrop(
                    phase: swipe.phase,
                    progress: swipe.progress,
                    isRead: false,
                    anchorY: anchorY
                )
            }
    }

    private var card: some View {
        RoundedRectangle(cornerRadius: CornerRadius.card, style: .continuous)
            .fill(
                LinearGradient(
                    colors: [Color(red: 0.62, green: 0.48, blue: 0.3), Color(red: 0.2, green: 0.15, blue: 0.1)],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
            .frame(height: 380)
    }
}
#endif
