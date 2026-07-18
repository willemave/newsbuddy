import MarkdownUI
import SwiftUI

struct BriefingDigSheet: View {
    @ObservedObject var viewModel: BriefingDigViewModel

    @State private var safariItem: BriefingSafariItem?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    fragmentHeader
                    stateContent
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 12)
                .padding(.bottom, 32)
            }
            .background(Color.surfacePrimary)
            .navigationTitle("Dig Deeper")
            .navigationBarTitleDisplayMode(.inline)
            .animation(.easeInOut(duration: 0.25), value: viewModel.stateKey)
            .accessibilityIdentifier("briefing.dig_sheet")
        }
        .sheet(item: $safariItem) { item in
            SafariView(url: item.url)
                .ignoresSafeArea()
        }
    }

    private var fragmentHeader: some View {
        Label(viewModel.fragment ?? "Dig Deeper", systemImage: "magnifyingglass")
            .font(.appCallout.weight(.semibold))
            .foregroundStyle(Color.onSurfaceSecondary)
            .lineLimit(2)
    }

    @ViewBuilder
    private var stateContent: some View {
        switch viewModel.state {
        case .idle:
            EmptyView()
        case .searching:
            loadingRow("Searching the web…")
                .transition(.opacity)
        case .summarizing(let results):
            VStack(alignment: .leading, spacing: 16) {
                loadingRow("Summarizing…")
                resultLinks(results)
            }
            .transition(.opacity)
        case .loaded(let results, let summary):
            VStack(alignment: .leading, spacing: 16) {
                Markdown(BriefingDigViewModel.citationLinkedMarkdown(summary))
                    .markdownTheme(.chat)
                    .environment(\.openURL, citationOpenAction(results: results))
                    .textSelection(.enabled)
                    .accessibilityIdentifier("briefing.dig_summary")
                resultLinks(results)
            }
            .transition(.opacity)
        case .error(let message):
            VStack(alignment: .leading, spacing: 12) {
                Text(message)
                    .font(.appCallout)
                    .foregroundStyle(Color.statusDestructive)
                Button("Try Again") {
                    viewModel.retry()
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("briefing.dig_retry")
            }
            .transition(.opacity)
        }
    }

    private func loadingRow(_ label: String) -> some View {
        HStack(spacing: 10) {
            ProgressView()
            Text(label)
                .font(.appCallout)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
    }

    private func citationOpenAction(results: [APIBriefingDigSearchResult]) -> OpenURLAction {
        OpenURLAction { url in
            guard url.scheme == "digsource",
                  let index = Int(url.host ?? ""),
                  index >= 1,
                  index <= results.count,
                  let sourceURL = URL(string: results[index - 1].url)
            else { return .systemAction }
            safariItem = BriefingSafariItem(url: sourceURL)
            return .handled
        }
    }

    @ViewBuilder
    private func resultLinks(_ results: [APIBriefingDigSearchResult]) -> some View {
        if !results.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                Text("Sources")
                    .kicker()
                ForEach(Array(results.prefix(5).enumerated()), id: \.offset) { _, result in
                    if let url = URL(string: result.url) {
                        Button {
                            safariItem = BriefingSafariItem(url: url)
                        } label: {
                            Text(result.title)
                                .font(.appCallout.weight(.semibold))
                                .foregroundStyle(Color.brandPrimary)
                                .lineLimit(2)
                                .multilineTextAlignment(.leading)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }
}
struct BriefingSourceSheetItem: Identifiable {
    let source: APIBriefingSource
    let initialScrollTarget: ContentDetailScrollTarget?

    init(
        source: APIBriefingSource,
        initialScrollTarget: ContentDetailScrollTarget? = nil
    ) {
        self.source = source
        self.initialScrollTarget = initialScrollTarget
    }

    var id: String {
        source.sourceKey
    }
}

struct BriefingSafariItem: Identifiable {
    let url: URL

    var id: String {
        url.absoluteString
    }
}

/// Every briefing source opens the same reading screen the feeds use —
/// news sources get the full short-news article view, not an abridged card.
struct BriefingSourceSheet: View {
    let item: BriefingSourceSheetItem
    let contentIds: [Int]

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ContentDetailView(
                contentId: item.source.id,
                contentType: item.source.contentType,
                allContentIds: contentIds,
                navigationSurface: .briefing,
                initialScrollTarget: item.initialScrollTarget
            )
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
    }
}
