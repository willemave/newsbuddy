import SwiftUI

private struct FilteredNewsRow: Identifiable, Sendable {
    let content: ContentSummary
    let status: String
    var id: Int { content.id }
}

struct NewsFilterNewsView: View {
    let service: any NewsFilterServicing
    let filtered: Bool
    @State private var feed: PaginatedFeed<FilteredNewsRow>

    init(service: any NewsFilterServicing, filtered: Bool) {
        self.service = service
        self.filtered = filtered
        _feed = State(initialValue: PaginatedFeed { cursor in
            let result = try await service.news(filtered: filtered, cursor: cursor)
            return Page(items: result.items.map {
                FilteredNewsRow(content: ContentSummary(api: $0.news), status: $0.filterStatus)
            }, nextCursor: result.nextCursor, hasMore: result.nextCursor != nil)
        })
    }

    var body: some View {
        let contentIds = feed.items.map(\.id)
        List {
            Section {
                NavigationLink("Edit News Filter") { NewsFilterView(service: service) }
                if !filtered {
                    NavigationLink("Filtered Out") { NewsFilterNewsView(service: service, filtered: true) }
                }
            }
            if feed.phase == .initialLoading { ProgressView() }
            if case .error(let message) = feed.phase {
                Section {
                    Text(message).foregroundStyle(Color.onSurfaceSecondary)
                    Button("Reload News") { Task { await feed.refresh() } }
                }
            }
            if feed.phase == .empty {
                Text(filtered ? "No News is currently filtered out." : "No News is ready yet.")
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            ForEach(feed.items) { row in
                NavigationLink(value: ContentDetailRoute(summary: row.content, allContentIds: contentIds)) {
                    VStack(alignment: .leading, spacing: 6) {
                        ContentCard(content: row.content, reservesThumbnailSpace: true)
                        Text(newsFilterStatusText(row.status))
                            .font(.modalCaption)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                }
                .buttonStyle(.plain)
                .appListRow()
            }
            if feed.phase == .loadingMore { ProgressView() }
        }
        .listStyle(.plain)
        .appNavigationTitle(filtered ? "Filtered Out" : "All News")
        .task { if feed.phase == .idle { await feed.loadInitial() } }
        .refreshable { await feed.refresh() }
        .onPaginationThresholdReached { await feed.loadNextPage() }
        .accessibilityIdentifier(filtered ? "news_filter.filtered_news" : "news_filter.all_news")
    }
}
