//
//  SavedLibraryView.swift
//  newsly
//

import SwiftUI

struct KnowledgeLibraryView: View {
    let showNavigationTitle: Bool

    @StateObject private var viewModel = ContentListViewModel(defaultReadFilter: "all")
    @State private var selectedTypeFilter: LibraryTypeFilter = .all
    @State private var selectedSort: LibrarySort = .newest

    init(showNavigationTitle: Bool = true) {
        self.showNavigationTitle = showNavigationTitle
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
                .font(.system(size: 48, weight: .light))
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
                    allContentIds: displayedContentIds
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
                            withAnimation(.easeOut(duration: 0.3)) {
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
            } else if viewModel.hasMore {
                Button {
                    Task { await viewModel.loadMoreContent() }
                } label: {
                    Label("Load more", systemImage: "chevron.down")
                        .font(.terracottaBodyMedium.weight(.semibold))
                        .foregroundStyle(Color.terracottaPrimary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                }
                .buttonStyle(.plain)
                .appListRow()
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
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
                            .font(.system(size: 11, weight: .semibold))
                        Text(selectedSort.shortTitle)
                    }
                    .font(.terracottaBodySmall.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
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
                            withAnimation(.easeOut(duration: 0.18)) {
                                selectedTypeFilter = filter
                            }
                        }
                    }
                }
                .padding(.trailing, Spacing.screenHorizontal)
            }
            .scrollClipDisabled()

            if hasActiveFilters {
                Button("Clear filter") {
                    withAnimation(.easeOut(duration: 0.18)) {
                        selectedTypeFilter = .all
                    }
                }
                .font(.terracottaBodySmall.weight(.semibold))
                .foregroundStyle(Color.brandPrimary)
                .padding(.horizontal, 2)
            }
        }
        .padding(.horizontal, Spacing.screenHorizontal)
        .padding(.top, 10)
        .padding(.bottom, 10)
        .appListRow()
    }

    private var filteredEmptyRow: some View {
        VStack(spacing: 10) {
            Image(systemName: "line.3.horizontal.decrease.circle")
                .font(.system(size: 28, weight: .light))
                .foregroundStyle(Color.onSurfaceSecondary)

            Text("No saved items match these filters")
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(Color.onSurface)

            Button("Clear filters") {
                withAnimation(.easeOut(duration: 0.18)) {
                    selectedTypeFilter = .all
                }
            }
            .font(.terracottaBodySmall.weight(.semibold))
            .foregroundStyle(Color.brandPrimary)
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

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Text(dateText)
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)
                .frame(width: 44, alignment: .leading)
                .lineLimit(1)
                .monospacedDigit()
                .padding(.top, 2)

            Text(content.displayTitle)
                .font(.terracottaBodyLarge.weight(.semibold))
                .foregroundStyle(Color.onSurface)
                .lineLimit(2)
                .truncationMode(.tail)

            if content.savedSource == "x_bookmark" {
                Image(systemName: "bookmark.fill")
                    .font(.system(size: 11, weight: .semibold))
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
                        .font(.system(size: 11, weight: .semibold))
                }
                Text(title)
            }
            .font(.terracottaBodySmall.weight(.semibold))
            .foregroundStyle(isSelected ? Color.surfacePrimary : Color.onSurface)
            .padding(.horizontal, 11)
            .padding(.vertical, 7)
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
            return content.contentTypeEnum == .article
        case .podcast:
            return content.contentTypeEnum == .podcast
        case .news:
            return content.contentTypeEnum == .news
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
