#if DEBUG
import SwiftUI

struct E2ENewsFilterVisualState: View {
    private let service = E2ENewsFilterService()
    var body: some View {
        NavigationStack { NewsFilterView(service: service) }
    }
}

private final class E2ENewsFilterService: NewsFilterServicing {
    private var preference = APINewsFilterPreference(
        revision: 1, text: "Include space exploration and climate research. Exclude celebrity news and sports.", enabled: true)
    func load() async throws -> APINewsFilterPreference { preference }
    func save(revision: Int, text: String, enabled: Bool) async throws -> APINewsFilterPreference {
        preference = APINewsFilterPreference(revision: revision + 1, text: text, enabled: enabled)
        return preference
    }
    func preview(text: String) async throws -> APINewsFilterPreviewResponse {
        APINewsFilterPreviewResponse(items: [
            previewItem(1, "NASA confirms Artemis III crew for lunar landing", "matched"),
            previewItem(2, "Arctic sea ice hits record low for October", "matched"),
            previewItem(3, "Pop star announces surprise stadium tour", "filtered_out"),
            previewItem(4, "Late goal sends league leaders through to the final", "filtered_out"),
        ])
    }
    private func previewItem(_ id: Int, _ title: String, _ status: String) -> APINewsFilterNewsItem {
        APINewsFilterNewsItem(
            news: APINewsItemSummaryResponse(
                id: id, contentType: .news, url: "https://example.com/\(id)", sourceUrl: nil, discussionUrl: nil,
                title: title, source: nil, platform: nil, status: .completed, shortSummary: nil, createdAt: Date(),
                processedAt: nil, classification: nil, publicationDate: nil, isRead: false, isSavedToKnowledge: false,
                newsArticleUrl: nil, newsDiscussionUrl: nil, newsKeyPoints: nil, newsSummary: nil, topComment: nil,
                commentCount: nil),
            filterStatus: status)
    }
    func news(filtered: Bool, cursor: String?) async throws -> APINewsFilterNewsResponse {
        APINewsFilterNewsResponse(items: [], nextCursor: nil)
    }
}
#endif
