#if DEBUG
import SwiftUI

/// Renders real recommendation cards with deterministic, backend-free evidence.
struct E2EAssistantSourceActivityVisualState: View {
    @State private var actionModel = AssistantFeedOptionActionModel(
        service: E2ESourceActivitySubscriber(),
        toastPresenter: E2ESourceActivityToastPresenter()
    )

    private static let checkedAt = Date(timeIntervalSince1970: 1_791_223_200)

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Discover sources")
                    .font(.appTitle2.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                Text("Publication and collection history help you compare subscriptions.")
                    .font(.appSubheadline)
                    .foregroundStyle(Color.onSurfaceSecondary)
                AssistantFeedOptionsSection(options: Self.options, actionModel: actionModel, onPreview: { _ in })
            }
            .padding(16)
        }
        .background(Color.surfacePrimary)
    }

    private static var options: [AssistantFeedOption] {
        [
            option(id: "newsletter", title: "The Production Agent", type: .substack,
                   description: "Practical lessons on deploying agents.",
                   activity: activity(basis: .feed_snapshot, title: "Managing an agent's memory",
                                      publishedDaysAgo: 2, quarter: 14, year: 51, undated: 1)),
            option(id: "podcast", title: "Systems in Practice", type: .podcast_rss,
                   description: "Conversations about reliable systems.",
                   activity: activity(basis: .feed_snapshot, title: "Durable jobs and retries",
                                      publishedDaysAgo: 100, quarter: 0, year: 8)),
            option(id: "aggregator", title: "Hugging Face Papers", type: .aggregator,
                   description: "Community-trending AI research papers.",
                   activity: activity(basis: .newsly_corpus, title: "Learning with useful examples",
                                      publishedDaysAgo: nil, quarter: 1_250, year: 6_088)),
            option(id: "unknown", title: "New Research Notes", type: .atom,
                   description: "A source whose publishing history is unavailable.", activity: nil),
        ]
    }

    private static func option(
        id: String, title: String, type: APIFeedType, description: String,
        activity: APIAssistantSourceActivity?
    ) -> AssistantFeedOption {
        AssistantFeedOption(
            id: id, title: title, siteUrl: "https://example.com/\(id)",
            feedUrl: type == .aggregator ? "aggregator://hfpapers" : "https://example.com/\(id)/feed",
            feedType: type, description: description, rationale: nil, evidenceUrl: nil,
            publishingActivity: activity
        )
    }

    private static func activity(
        basis: APISourceActivityBasis, title: String, publishedDaysAgo: Int?,
        quarter: Int?, year: Int?, undated: Int = 0
    ) -> APIAssistantSourceActivity {
        APIAssistantSourceActivity(
            basis: basis, checkedAt: checkedAt,
            windowStart: checkedAt.addingTimeInterval(-90 * 86_400),
            yearWindowStart: checkedAt.addingTimeInterval(-365 * 86_400), windowEnd: checkedAt,
            latestItem: APIAssistantPublishedItem(
                title: title, url: "https://example.com/latest",
                publishedAt: publishedDaysAgo.map { checkedAt.addingTimeInterval(-Double($0) * 86_400) },
                collectedAt: basis == .newsly_corpus ? checkedAt.addingTimeInterval(-86_400) : nil
            ),
            observedCount: quarter, observedYearCount: year, undatedCount: undated
        )
    }
}

@MainActor
private final class E2ESourceActivitySubscriber: AssistantFeedSubscribing {
    func subscribeFeed(feedURL: String, feedType: String, displayName: String?) async throws -> ScraperConfig {
        throw CancellationError()
    }
}

@MainActor
private final class E2ESourceActivityToastPresenter: ToastPresenting {
    func show(_ message: String, type: ToastType, duration: TimeInterval) {}
    func showError(_ message: String) {}
    func showSuccess(_ message: String) {}
}
#endif
