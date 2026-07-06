//
//  SavedLibraryView.swift
//  newsly
//

import SwiftUI

struct KnowledgeLibraryView: View {
    let showNavigationTitle: Bool
    let readStateCache: ReadStateCache

    @State private var viewModel: ContentListViewModel
    @State private var selectedTypeFilter: LibraryTypeFilter = .all
    @State private var selectedSort: LibrarySort = .newest

    init(showNavigationTitle: Bool = true, readStateCache: ReadStateCache? = nil) {
        let readStateCache = readStateCache ?? ReadStateCache()
        self.showNavigationTitle = showNavigationTitle
        self.readStateCache = readStateCache
        self._viewModel = State(
            initialValue: RootDependencyFactory.makeContentListViewModel(
                defaultReadFilter: "all",
                readStateCache: readStateCache
            )
        )
    }

    private var visibleContents: [ContentSummary] {
        let filtered = viewModel.contents.filter { content in
            selectedTypeFilter.matches(content)
        }

        return filtered.sorted { lhs, rhs in
            switch selectedSort {
            case .newest:
                if lhs.itemDate == rhs.itemDate {
                    return lhs.id > rhs.id
                }
                return (lhs.itemDate ?? .distantPast) > (rhs.itemDate ?? .distantPast)
            case .oldest:
                if lhs.itemDate == rhs.itemDate {
                    return lhs.id < rhs.id
                }
                return (lhs.itemDate ?? .distantPast) < (rhs.itemDate ?? .distantPast)
            }
        }
    }

    private var availableTypeFilters: [LibraryTypeFilter] {
        var filters: [LibraryTypeFilter] = [.all]
        for filter in LibraryTypeFilter.contentFilters
            where viewModel.contents.contains(where: { filter.matches($0) }) {
            filters.append(filter)
        }
        if !filters.contains(selectedTypeFilter) {
            filters.append(selectedTypeFilter)
        }
        return filters
    }

    private var hasActiveFilters: Bool {
        selectedTypeFilter != .all
    }

    var body: some View {
        Group {
            if viewModel.isLoading && viewModel.contents.isEmpty {
                LoadingView()
            } else if let error = viewModel.errorMessage, viewModel.contents.isEmpty {
                ErrorView(message: error) {
                    Task { await viewModel.loadKnowledgeLibrary() }
                }
            } else if viewModel.contents.isEmpty {
                emptyState
            } else {
                contentList
            }
        }
        .background(Color.surfacePrimary)
        .navigationTitle(showNavigationTitle ? "Saved" : "")
        .task { await viewModel.loadKnowledgeLibrary() }
    }

    // MARK: - Empty State

    private var emptyState: some View {
        VStack(spacing: 20) {
            Image(systemName: "books.vertical")
                .font(.appSymbol(size: 48, weight: .light))
                .foregroundStyle(Color.onSurfaceTertiary.opacity(0.78))

            VStack(spacing: 6) {
                Text("No saved items yet")
                    .font(.listTitle.weight(.semibold))
                    .foregroundStyle(Color.onSurface)

                Text("Bookmarks and saved knowledge will show up here.")
                    .font(.listSubtitle)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 280)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
    }

    // MARK: - Content List

    private var contentList: some View {
        let displayedContents = visibleContents
        let displayedContentIds = displayedContents.map(\.id)

        return List {
            libraryControls(visibleCount: displayedContents.count)

            if displayedContents.isEmpty {
                filteredEmptyRow
            }

            ForEach(displayedContents) { content in
                NavigationLink(destination: ContentDetailView(
                    contentId: content.id,
                    contentType: content.contentType,
                    allContentIds: displayedContentIds,
                    navigationSurface: .savedLibrary,
                    readStateCache: readStateCache
                )) {
                    SavedLibraryRow(content: content)
                }
                .buttonStyle(.plain)
                .appListRow()
                .swipeActions(edge: .leading, allowsFullSwipe: true) {
                    if !content.isRead {
                        Button {
                            Task { await viewModel.markAsRead(content.id) }
                        } label: {
                            Label("Mark as Read", systemImage: "checkmark.circle.fill")
                        }
                        .tint(Color.brandPrimary)
                    }
                }
                .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                    Button {
                        Task {
                            await viewModel.toggleKnowledgeSave(content.id)
                            withAnimation(AppMotion.panel) {
                                viewModel.contents.removeAll { $0.id == content.id }
                            }
                        }
                    } label: {
                        Label("Remove", systemImage: "books.vertical.fill")
                    }
                    .tint(Color.statusDestructive)
                }
            }

            if viewModel.isLoadingMore {
                HStack {
                    Spacer()
                    ProgressView()
                        .padding()
                    Spacer()
                }
                .appListRow()
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .onPaginationThresholdReached {
            await viewModel.loadMoreContent()
        }
        .refreshable { await viewModel.loadKnowledgeLibrary() }
    }

    private func libraryControls(visibleCount: Int) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 12) {
                Text("\(visibleCount) saved")
                    .font(.terracottaBodySmall.weight(.semibold))
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .lineLimit(1)
                    .monospacedDigit()

                Spacer()

                Menu {
                    ForEach(LibrarySort.allCases) { sort in
                        Button {
                            selectedSort = sort
                        } label: {
                            if selectedSort == sort {
                                Label(sort.title, systemImage: "checkmark")
                            } else {
                                Text(sort.title)
                            }
                        }
                    }
                } label: {
                    HStack(spacing: 5) {
                        Image(systemName: "arrow.up.arrow.down")
                            .font(.appSymbol(size: 11, weight: .semibold))
                        Text(selectedSort.shortTitle)
                    }
                    .font(.terracottaBodySmall.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .frame(minHeight: 44)
                    .background(Color.surfaceSecondary, in: Capsule())
                    .overlay(
                        Capsule()
                            .stroke(Color.outlineVariant.opacity(0.45), lineWidth: 1)
                    )
                }
            }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(availableTypeFilters) { filter in
                        LibraryFilterPill(
                            title: filter.title,
                            systemImage: filter.systemImage,
                            isSelected: selectedTypeFilter == filter
                        ) {
                            withAnimation(AppMotion.subtle) {
                                selectedTypeFilter = filter
                            }
                        }
                    }
                }
                .padding(.trailing, Spacing.appHorizontalMargin)
            }
            .scrollClipDisabled()

            if hasActiveFilters {
                Button("Clear filter") {
                    withAnimation(AppMotion.subtle) {
                        selectedTypeFilter = .all
                    }
                }
                .font(.terracottaBodySmall.weight(.semibold))
                .foregroundStyle(Color.brandPrimary)
                .padding(.horizontal, 2)
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 10)
        .padding(.bottom, 10)
        .appListRow()
    }

    private var filteredEmptyRow: some View {
        VStack(spacing: 10) {
            Image(systemName: "line.3.horizontal.decrease.circle")
                .font(.appSymbol(size: 28, weight: .light))
                .foregroundStyle(Color.onSurfaceSecondary)

            Text("No saved items match these filters")
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(Color.onSurface)

            Button("Clear filters") {
                withAnimation(AppMotion.subtle) {
                    selectedTypeFilter = .all
                }
            }
            .font(.terracottaBodySmall.weight(.semibold))
            .foregroundStyle(Color.brandPrimary)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 32)
        .appListRow()
    }
}

// MARK: - Saved Library Row

private struct SavedLibraryRow: View {
    let content: ContentSummary

    private var dateText: String {
        ContentTimestampFormatter.detailMetaText(from: content.primaryTimestamp)
            ?? content.processedDateDisplay
            ?? content.formattedDate
    }

    private var sourceText: String? {
        for candidate in [content.source, content.platform] {
            guard let candidate else { continue }
            let trimmed = candidate.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                return trimmed
            }
        }
        return nil
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Text(dateText)
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)
                .frame(width: 44, alignment: .leading)
                .lineLimit(1)
                .monospacedDigit()
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 4) {
                Text(content.displayTitle)
                    .font(.terracottaBodyLarge.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(2)
                    .truncationMode(.tail)

                if let sourceText {
                    Text(sourceText)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if content.savedSource == "x_bookmark" {
                Image(systemName: "bookmark.fill")
                    .font(.appSymbol(size: 11, weight: .semibold))
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .padding(.top, 4)
                    .accessibilityLabel("X bookmark")
            }
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, 8)
        .frame(minHeight: 52, alignment: .center)
        .contentShape(Rectangle())
    }
}

private struct LibraryFilterPill: View {
    let title: String
    var systemImage: String?
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                if let systemImage {
                    Image(systemName: systemImage)
                        .font(.appSymbol(size: 11, weight: .semibold))
                }
                Text(title)
            }
            .font(.terracottaBodySmall.weight(.semibold))
            .foregroundStyle(isSelected ? Color.surfacePrimary : Color.onSurface)
            .padding(.horizontal, 11)
            .padding(.vertical, 7)
            .frame(minHeight: 44)
            .background(isSelected ? Color.brandPrimary : Color.surfaceSecondary, in: Capsule())
            .overlay(
                Capsule()
                    .stroke(Color.outlineVariant.opacity(isSelected ? 0 : 0.45), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

private enum LibraryTypeFilter: String, CaseIterable, Identifiable {
    case all
    case bookmarks
    case article
    case podcast
    case news

    static var contentFilters: [LibraryTypeFilter] {
        [.bookmarks, .article, .podcast, .news]
    }

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: return "All saved"
        case .bookmarks: return "Bookmarks"
        case .article: return "Articles"
        case .podcast: return "Podcasts"
        case .news: return "News"
        }
    }

    var systemImage: String? {
        switch self {
        case .all: return nil
        case .bookmarks: return "bookmark"
        case .article: return "doc.text"
        case .podcast: return "headphones"
        case .news: return "newspaper"
        }
    }

    func matches(_ content: ContentSummary) -> Bool {
        switch self {
        case .all:
            return true
        case .bookmarks:
            return content.savedSource == "x_bookmark"
        case .article:
            return content.contentType == .article
        case .podcast:
            return content.contentType == .podcast
        case .news:
            return content.contentType == .news
        }
    }
}

private enum LibrarySort: String, CaseIterable, Identifiable {
    case newest
    case oldest

    var id: String { rawValue }

    var title: String {
        switch self {
        case .newest: return "Newest first"
        case .oldest: return "Oldest first"
        }
    }

    var shortTitle: String {
        switch self {
        case .newest: return "Newest"
        case .oldest: return "Oldest"
        }
    }
}
