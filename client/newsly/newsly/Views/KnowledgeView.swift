//
//  KnowledgeView.swift
//  newsly
//

import SwiftUI

struct KnowledgeSearchRoute: Hashable {}

struct KnowledgeView: View {
    let onSelectContent: (ContentDetailRoute) -> Void
    let onSearch: () -> Void
    var onOpenMore: (() -> Void)?

    @State private var viewModel: ContentListViewModel
    @State private var settings = AppSettings.shared

    init(
        onSelectContent: @escaping (ContentDetailRoute) -> Void,
        onSearch: @escaping () -> Void,
        onOpenMore: (() -> Void)? = nil,
        viewModel: ContentListViewModel? = nil,
        readStateCache: ReadStateCache? = nil
    ) {
        self.onSelectContent = onSelectContent
        self.onSearch = onSearch
        self.onOpenMore = onOpenMore
        self._viewModel = State(
            initialValue: viewModel ?? RootDependencyFactory.makeContentListViewModel(
                readStateCache: readStateCache ?? ReadStateCache()
            )
        )
    }

    private var appTextSize: DynamicTypeSize {
        AppTextSize(index: settings.appTextSizeIndex).dynamicTypeSize
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                EditorialMastheadHeader(
                    title: "Knowledge",
                    trailingAccessory: AnyView(headerActions)
                )

                Text("RECENTLY SAVED")
                    .kicker()
                    .accessibilityLabel("Recently Saved")
                    .padding(.horizontal, Spacing.appHorizontalMargin)
                    .padding(.bottom, 12)

                libraryContent
            }
            .padding(.bottom, 32)
        }
        .accessibilityIdentifier("knowledge.screen")
        .onPaginationThresholdReached {
            await viewModel.loadMoreContent()
        }
        .refreshable { await viewModel.loadKnowledgeLibrary() }
        .topScreenEdgeFade()
        .bottomScreenEdgeFade()
        .dynamicTypeSize(appTextSize)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .task { await viewModel.loadKnowledgeLibrary() }
    }

    private var headerActions: some View {
        HStack(spacing: 2) {
            Button(action: onSearch) {
                Image(systemName: "magnifyingglass")
                    .font(.appSymbol(size: 19, weight: .semibold))
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
            .accessibilityLabel("Search saved knowledge")
            .accessibilityIdentifier("knowledge.search")

            if let onOpenMore {
                Button(action: onOpenMore) {
                    Image(systemName: "line.3.horizontal")
                        .font(.appSymbol(size: 19, weight: .semibold))
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
                .accessibilityLabel("Settings and more")
                .accessibilityIdentifier("knowledge.more_menu")
            }
        }
        .foregroundStyle(Color.onSurface)
    }

    @ViewBuilder
    private var libraryContent: some View {
        if viewModel.isLoading && viewModel.contents.isEmpty {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text("Loading saved articles")
                    .font(.terracottaBodyMedium)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 20)
        } else if let errorMessage = viewModel.errorMessage, viewModel.contents.isEmpty {
            StateView(
                role: .error(message: errorMessage),
                actionTitle: "Try Again",
                action: { Task { await viewModel.loadKnowledgeLibrary() } }
            )
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 28)
        } else if viewModel.contents.isEmpty {
            EmptyStateView(
                icon: "bookmark",
                title: "Nothing saved yet",
                subtitle: "Articles you save will appear here with their artwork."
            )
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 28)
        } else {
            let readyContentIDs = viewModel.contents.compactMap { content in
                content.savedLibraryItemState == .ready ? content.id : nil
            }

            ForEach(viewModel.contents) { content in
                KnowledgeSavedContentButton(
                    content: content,
                    allContentIds: readyContentIDs,
                    accessibilityIdentifier: "knowledge.saved.\(content.id)",
                    onSelectContent: onSelectContent,
                    onRemove: { Task { await viewModel.toggleKnowledgeSave(content.id) } }
                )

                if content.id != viewModel.contents.last?.id {
                    Divider()
                        .padding(.leading, Spacing.appHorizontalMargin + 104)
                        .padding(.trailing, Spacing.appHorizontalMargin)
                }
            }

            if viewModel.isLoadingMore {
                ProgressView()
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
            }
        }
    }
}

struct KnowledgeSearchView: View {
    let onSelectContent: (ContentDetailRoute) -> Void

    @State private var viewModel: ContentListViewModel
    @State private var query = ""
    @FocusState private var isSearchFocused: Bool

    init(
        onSelectContent: @escaping (ContentDetailRoute) -> Void,
        readStateCache: ReadStateCache
    ) {
        self.onSelectContent = onSelectContent
        self._viewModel = State(
            initialValue: RootDependencyFactory.makeContentListViewModel(
                readStateCache: readStateCache
            )
        )
    }

    private var trimmedQuery: String {
        query.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        VStack(spacing: 0) {
            searchField
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.vertical, 10)

            Divider()

            List {
                if trimmedQuery.count < 2 {
                    EmptyStateView(
                        icon: "magnifyingglass",
                        title: "Search your saves",
                        subtitle: "Search by title, source, or URL."
                    )
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
                } else if viewModel.isLoading {
                    ProgressView("Searching")
                        .font(.appSubheadline)
                        .frame(maxWidth: .infinity)
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                } else if viewModel.contents.isEmpty {
                    EmptyStateView(
                        icon: "magnifyingglass",
                        title: "No results",
                        subtitle: "No saved items match “\(trimmedQuery)”."
                    )
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
                } else {
                    let readyContentIDs = viewModel.contents.compactMap { content in
                        content.savedLibraryItemState == .ready ? content.id : nil
                    }

                    ForEach(viewModel.contents) { content in
                        KnowledgeSavedContentButton(
                            content: content,
                            allContentIds: readyContentIDs,
                            accessibilityIdentifier: "knowledge.search.result.\(content.id)",
                            onSelectContent: onSelectContent,
                            onRemove: { Task { await viewModel.toggleKnowledgeSave(content.id) } }
                        )
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                    }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
        }
        .background(Color.surfacePrimary)
        .onPaginationThresholdReached {
            await viewModel.loadMoreContent()
        }
        .appNavigationTitle("Search Knowledge")
        .task {
            await Task.yield()
            isSearchFocused = true
        }
        .task(id: trimmedQuery) {
            guard trimmedQuery.count >= 2 else {
                viewModel.clearKnowledgeLibrary()
                return
            }
            try? await Task.sleep(for: .milliseconds(250))
            guard !Task.isCancelled else { return }
            await viewModel.loadKnowledgeLibrary(query: trimmedQuery)
        }
    }

    private var searchField: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .font(.appSymbol(size: 15, weight: .semibold))
                .foregroundStyle(Color.onSurfaceSecondary)
                .accessibilityHidden(true)

            TextField("Search saved knowledge", text: $query)
                .font(.appBody)
                .focused($isSearchFocused)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .accessibilityIdentifier("knowledge.search.input")

            if !query.isEmpty {
                Button {
                    query = ""
                    isSearchFocused = true
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.appSymbol(size: 16))
                        .foregroundStyle(Color.onSurfaceTertiary)
                        .frame(width: 32, height: 44)
                }
                .buttonStyle(.plain)
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
                .accessibilityLabel("Clear search")
            }
        }
        .padding(.leading, 14)
        .padding(.trailing, query.isEmpty ? 14 : 6)
        .frame(minHeight: 48)
        .background(Color.surfaceContainerHighest)
        .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
                .stroke(Color.outlineVariant.opacity(0.35), lineWidth: 1)
        }
    }

}

private struct KnowledgeSavedContentButton: View {
    let content: ContentSummary
    let allContentIds: [Int]
    let accessibilityIdentifier: String
    let onSelectContent: (ContentDetailRoute) -> Void
    let onRemove: () -> Void

    var body: some View {
        Button {
            guard content.savedLibraryItemState == .ready else { return }
            onSelectContent(
                ContentDetailRoute(
                    summary: content,
                    allContentIds: allContentIds,
                    navigationSurface: .savedLibrary
                )
            )
        } label: {
            KnowledgeSavedRow(content: content)
        }
        .buttonStyle(.plain)
        .disabled(content.savedLibraryItemState != .ready)
        .contextMenu {
            Button(role: .destructive, action: onRemove) {
                Label("Remove from Knowledge", systemImage: "bookmark.slash")
            }
        }
        .accessibilityIdentifier(accessibilityIdentifier)
    }
}

private struct KnowledgeSavedRow: View {
    let content: ContentSummary

    private let imageSize = CGSize(width: 92, height: 76)

    private var hasStalled: Bool {
        guard content.savedLibraryItemState == .processing,
              let createdAt = ContentTimestampFormatter.parse(content.createdAt)
        else { return false }
        return AppClock.now.timeIntervalSince(createdAt) > 24 * 60 * 60
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            artwork

            VStack(alignment: .leading, spacing: 5) {
                Text(content.displayTitle)
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(3)
                    .truncationMode(.tail)
                    .fixedSize(horizontal: false, vertical: true)

                // Stalled and unavailable items are common enough in a saved list that
                // alarm coloring on every row reads as an emergency. State is carried by
                // the glyph and label; recovery lives in the detail view.
                if hasStalled {
                    Label("Preparation stalled", systemImage: "exclamationmark.circle")
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceTertiary)
                } else if content.savedLibraryItemState == .processing {
                    Label("Preparing", systemImage: "hourglass")
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                } else if content.savedLibraryItemState == .unavailable {
                    Label("Unavailable", systemImage: "exclamationmark.circle")
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceTertiary)
                } else if let summary = content.summaryDisplayText {
                    Text(summary)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(2)
                }

                HStack(spacing: 5) {
                    ForEach(Array(content.knowledgeSourceLabels.enumerated()), id: \.offset) { index, source in
                        if index > 0 {
                            Text("·")
                                .foregroundStyle(Color.onSurfaceTertiary)
                        }
                        // Metadata, not a link — accent here made source names look tappable.
                        Text(source.uppercased())
                            .kicker(color: .onSurfaceTertiary)
                            .lineLimit(1)
                    }
                    if let relativeTime = content.relativeTimeDisplay {
                        Text("·")
                            .foregroundStyle(Color.onSurfaceTertiary)
                        Text(relativeTime)
                            .foregroundStyle(Color.onSurfaceTertiary)
                    }
                }
                .font(.terracottaLabelSmall)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Image(systemName: "chevron.right")
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.onSurfaceTertiary)
                .padding(.top, 4)
                .opacity(content.savedLibraryItemState == .ready ? 1 : 0)
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 11)
        .contentShape(Rectangle())
    }

    private var artwork: some View {
        CachedAsyncImage(
            url: content.imageUrl.flatMap(ServerImageURL.resolve),
            thumbnailUrl: content.thumbnailUrl.flatMap(ServerImageURL.resolve),
            targetSize: imageSize
        ) { image in
            image
                .resizable()
                .aspectRatio(contentMode: .fill)
                .frame(width: imageSize.width, height: imageSize.height)
                .clipped()
                .listThumbnailTreatment()
        } placeholder: {
            ZStack {
                Color.surfaceSecondary
                if hasStalled {
                    Image(systemName: "exclamationmark.circle")
                        .font(.appSymbol(size: 18, weight: .medium))
                        .foregroundStyle(Color.onSurfaceTertiary)
                } else if content.savedLibraryItemState == .processing {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "photo")
                        .font(.appSymbol(size: 18))
                        .foregroundStyle(Color.onSurfaceTertiary)
                }
            }
            .frame(width: imageSize.width, height: imageSize.height)
        }
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.outlineVariant.opacity(0.45), lineWidth: 0.5)
        }
    }
}

#Preview {
    NavigationStack {
        KnowledgeView(onSelectContent: { _ in }, onSearch: {})
    }
}
