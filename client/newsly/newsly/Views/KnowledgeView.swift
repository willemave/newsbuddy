//
//  KnowledgeView.swift
//  newsly
//

import SwiftUI

struct KnowledgeSearchRoute: Hashable {}

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
                    ForEach(viewModel.contents) { content in
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
        .background(Color.surfaceSecondary)
        .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
                .stroke(Color.borderSubtle, lineWidth: 1)
        }
    }

}

struct KnowledgeSavedContentButton: View {
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

struct KnowledgeSavedRow: View {
    let content: ContentSummary

    private let imageSize = CGSize(width: 40, height: 40)

    private var hasStalled: Bool {
        content.hasStalledKnowledgePreparation
    }

    private var artworkURL: URL? {
        (content.thumbnailUrl ?? content.imageUrl).flatMap(ServerImageURL.resolve)
    }

    private var subtitle: String? {
        content.savedLibraryItemState == .ready ? content.summaryDisplayText : nil
    }

    private var kickerText: String {
        let detail: String
        if hasStalled {
            detail = "PREPARATION STALLED"
        } else {
            switch content.savedLibraryItemState {
            case .processing: detail = "PREPARING"
            case .unavailable: detail = "UNAVAILABLE"
            case .ready: detail = content.knowledgeSourceLabels.first?.uppercased() ?? "SAVED"
            }
        }
        var parts = ["SAVED"]
        if detail != "SAVED" {
            parts.append(detail)
        }
        if let time = content.knowledgeRelativeTimeDisplay?.uppercased() {
            parts.append(time)
        }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        HStack(spacing: 12) {
            artwork

            VStack(alignment: .leading, spacing: 2) {
                Text(content.displayTitle)
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(1)
                    .truncationMode(.tail)

                if let subtitle {
                    Text(subtitle)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }

                Text(kickerText)
                    .kicker(color: .onSurfaceTertiary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Image(
                systemName: content.savedLibraryItemState == .ready
                    ? "chevron.right"
                    : "info.circle"
            )
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.onSurfaceTertiary)
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 8)
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
                        .font(.appSymbol(size: 16, weight: .medium))
                        .foregroundStyle(Color.onSurfaceTertiary)
                } else if content.savedLibraryItemState == .processing {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "photo")
                        .font(.appSymbol(size: 16))
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
