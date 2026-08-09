//
//  KnowledgeView.swift
//  newsly
//

import SwiftUI

struct KnowledgeSearchRoute: Hashable {}

struct KnowledgeView: View {
    let scrollToTopRequest: Int
    let onSelectContent: (ContentDetailRoute) -> Void
    let onSearch: () -> Void
    var onOpenMore: (() -> Void)?

    @State private var viewModel: ContentListViewModel
    @State private var settings = AppSettings.shared

    private static let topAnchor = "knowledge.top"

    init(
        scrollToTopRequest: Int = 0,
        onSelectContent: @escaping (ContentDetailRoute) -> Void,
        onSearch: @escaping () -> Void,
        onOpenMore: (() -> Void)? = nil,
        viewModel: ContentListViewModel? = nil,
        readStateCache: ReadStateCache? = nil
    ) {
        self.scrollToTopRequest = scrollToTopRequest
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
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    EditorialMastheadHeader(
                        title: "Knowledge",
                        titleAccessibilityIdentifier: "knowledge.screen",
                        trailingAccessory: AnyView(headerActions)
                    )
                    .id(Self.topAnchor)

                    Text("RECENTLY SAVED")
                        .kicker()
                        .accessibilityLabel("Recently Saved")
                        .padding(.horizontal, Spacing.appHorizontalMargin)
                        .padding(.bottom, 12)

                    libraryContent
                }
                .padding(.bottom, 32)
            }
            .onPaginationThresholdReached {
                await viewModel.loadMoreContent()
            }
            .refreshable { await viewModel.loadKnowledgeLibrary() }
            .topScreenEdgeFade()
            .bottomScreenEdgeFade()
            .scrollsToTopOnRequest(
                scrollToTopRequest,
                anchor: Self.topAnchor,
                using: proxy
            )
        }
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
            let contents = viewModel.contents
            let lastContentID = contents.last?.id

            if let errorMessage = viewModel.errorMessage {
                KnowledgeInlineError(
                    message: errorMessage,
                    actionTitle: viewModel.hasActionError ? "Dismiss" : "Try Again",
                    accessibilityIdentifier: "knowledge.error.inline",
                    action: viewModel.hasActionError
                        ? viewModel.clearActionError
                        : { Task { await viewModel.loadKnowledgeLibrary() } }
                )
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.bottom, 12)
            }

            ForEach(contents) { content in
                KnowledgeSavedContentButton(
                    content: content,
                    accessibilityIdentifier: "knowledge.saved.\(content.id)",
                    onOpen: {
                        onSelectContent(
                            ContentDetailRoute(
                                summary: content,
                                allContentIds: viewModel.readyContentIDs,
                                navigationSurface: .savedLibrary
                            )
                        )
                    },
                    onRefresh: { Task { await viewModel.loadKnowledgeLibrary() } },
                    onRemove: { Task { await viewModel.toggleKnowledgeSave(content.id) } }
                )

                if content.id != lastContentID {
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
                } else if let errorMessage = viewModel.errorMessage {
                    StateView(
                        role: .error(message: errorMessage),
                        actionTitle: "Try Again",
                        action: {
                            Task {
                                await viewModel.loadKnowledgeLibrary(query: trimmedQuery)
                            }
                        }
                    )
                    .accessibilityIdentifier("knowledge.search.error")
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
                    let contents = viewModel.contents

                    ForEach(contents) { content in
                        KnowledgeSavedContentButton(
                            content: content,
                            accessibilityIdentifier: "knowledge.search.result.\(content.id)",
                            onOpen: {
                                onSelectContent(
                                    ContentDetailRoute(
                                        summary: content,
                                        allContentIds: viewModel.readyContentIDs,
                                        navigationSurface: .savedLibrary
                                    )
                                )
                            },
                            onRefresh: {
                                Task { await viewModel.loadKnowledgeLibrary(query: trimmedQuery) }
                            },
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

private struct KnowledgeInlineError: View {
    let message: String
    let actionTitle: String
    let accessibilityIdentifier: String
    let action: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.circle")
                .foregroundStyle(Color.statusDestructive)
                .accessibilityHidden(true)
            Text(message)
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)
            Spacer(minLength: 8)
            Button(actionTitle, action: action)
                .buttonStyle(.bordered)
                .accessibilityIdentifier("\(accessibilityIdentifier).action")
        }
        .accessibilityIdentifier(accessibilityIdentifier)
    }
}

private struct KnowledgeSavedContentButton: View {
    let content: ContentSummary
    let accessibilityIdentifier: String
    let onOpen: () -> Void
    let onRefresh: () -> Void
    let onRemove: () -> Void

    @State private var showsPreparationStatus = false

    var body: some View {
        Button {
            guard content.savedLibraryItemState == .ready else {
                showsPreparationStatus = true
                return
            }
            onOpen()
        } label: {
            KnowledgeSavedRow(content: content)
        }
        .buttonStyle(.plain)
        .contextMenu {
            Button(role: .destructive, action: onRemove) {
                Label("Remove from Knowledge", systemImage: "bookmark.slash")
            }
        }
        .accessibilityIdentifier(accessibilityIdentifier)
        .accessibilityHint(
            content.savedLibraryItemState == .ready
                ? "Opens this saved item"
                : "Shows preparation status and recovery actions"
        )
        .sheet(isPresented: $showsPreparationStatus) {
            KnowledgePreparationStatusSheet(
                content: content,
                onRefresh: onRefresh,
                onRemove: onRemove
            )
        }
    }
}

private struct KnowledgeSavedRow: View {
    let content: ContentSummary

    private let imageSize = CGSize(width: 92, height: 76)

    private var hasStalled: Bool {
        content.hasStalledKnowledgePreparation
    }

    private var artworkURL: URL? {
        (content.thumbnailUrl ?? content.imageUrl).flatMap(ServerImageURL.resolve)
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

            Image(
                systemName: content.savedLibraryItemState == .ready
                    ? "chevron.right"
                    : "info.circle"
            )
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.onSurfaceTertiary)
                .padding(.top, 4)
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 11)
        .contentShape(Rectangle())
    }

    private var artwork: some View {
        CachedAsyncImage(
            url: artworkURL,
            targetSize: imageSize
        ) { image in
            image
                .resizable()
                .aspectRatio(contentMode: .fill)
                .frame(width: imageSize.width, height: imageSize.height)
                .clipped()
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

private struct KnowledgePreparationStatusSheet: View {
    @Environment(\.dismiss) private var dismiss

    let content: ContentSummary
    let onRefresh: () -> Void
    let onRemove: () -> Void

    @State private var browserDestination: BrowserDestination?

    private var title: String {
        if content.hasStalledKnowledgePreparation {
            return "Preparation stalled"
        }
        switch content.savedLibraryItemState {
        case .processing:
            return "Preparing this item"
        case .unavailable:
            return "Item unavailable"
        case .ready:
            return "Ready to read"
        }
    }

    private var message: String {
        if content.hasStalledKnowledgePreparation {
            return "Newsly has not finished preparing this save. Refresh its status or open the original source."
        }
        switch content.savedLibraryItemState {
        case .processing:
            return "Newsly is still preparing this save. You can refresh its status or read the original source now."
        case .unavailable:
            return "Newsly could not prepare this save, but the original source may still be available."
        case .ready:
            return "This save is ready."
        }
    }

    private var originalURL: URL? {
        guard let url = URL(string: content.url),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https"
        else { return nil }
        return url
    }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 20) {
                Label(title, systemImage: content.hasStalledKnowledgePreparation ? "exclamationmark.circle" : "hourglass")
                    .font(.terracottaHeadlineMedium)
                    .accessibilityIdentifier("knowledge.status.screen")

                Text(message)
                    .font(.terracottaBodyMedium)
                    .foregroundStyle(Color.onSurfaceSecondary)

                Button("Refresh status") {
                    onRefresh()
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("knowledge.status.refresh")

                if let originalURL {
                    Button("Open original") {
                        browserDestination = BrowserDestination(url: originalURL)
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("knowledge.status.open_original")
                }

                Button("Remove from Knowledge", role: .destructive) {
                    onRemove()
                    dismiss()
                }
                .accessibilityIdentifier("knowledge.status.remove")

                Spacer(minLength: 0)
            }
            .padding(Spacing.appHorizontalMargin)
            .background(Color.surfacePrimary.ignoresSafeArea())
            .navigationTitle("Saved item status")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium])
        .sheet(item: $browserDestination) { destination in
            SafariView(url: destination.url)
        }
    }
}

private extension ContentSummary {
    var hasStalledKnowledgePreparation: Bool {
        guard savedLibraryItemState == .processing,
              let createdAt = ContentTimestampFormatter.parse(createdAt)
        else { return false }
        return AppClock.now.timeIntervalSince(createdAt) > 24 * 60 * 60
    }
}

#Preview {
    NavigationStack {
        KnowledgeView(onSelectContent: { _ in }, onSearch: {})
    }
}
