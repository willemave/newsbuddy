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
        viewModel: ContentListViewModel
    ) {
        self.onSelectContent = onSelectContent
        self._viewModel = State(initialValue: viewModel)
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
                            entry: KnowledgeSavedTimelineEntry(content: content),
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
                            onReprocess: { await viewModel.reprocessKnowledgeItem(content.id) },
                            onRemove: { Task { await viewModel.toggleKnowledgeSave(content.id) } }
                        )
                        .contextMenu {
                            Button(role: .destructive) {
                                Task { await viewModel.toggleKnowledgeSave(content.id) }
                            } label: {
                                Label("Remove from Knowledge", systemImage: "bookmark.slash")
                            }
                        }
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
    let entry: KnowledgeSavedTimelineEntry
    let accessibilityIdentifier: String
    let onOpen: () -> Void
    let onRefresh: () -> Void
    let onReprocess: () async -> Bool
    let onRemove: () -> Void
    var derivatives: KnowledgeSourceDerivatives = []

    @State private var showsPreparationStatus = false

    private var content: ContentSummary { entry.content }

    var body: some View {
        Button {
            guard content.savedLibraryItemState == .ready else {
                showsPreparationStatus = true
                return
            }
            onOpen()
        } label: {
            KnowledgeSavedRow(entry: entry, derivatives: derivatives)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(accessibilityIdentifier)
        .accessibilityValue(
            [derivatives.accessibilitySummary, entry.statusText]
                .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: ", ")
        )
        .accessibilityHint(
            content.savedLibraryItemState == .ready
                ? "Opens this saved item"
                : "Shows preparation status and recovery actions"
        )
        .sheet(isPresented: $showsPreparationStatus) {
            KnowledgePreparationStatusSheet(
                content: content,
                onRefresh: onRefresh,
                onReprocess: onReprocess,
                onRemove: onRemove
            )
        }
    }
}

struct KnowledgeSavedRow: View {
    let entry: KnowledgeSavedTimelineEntry
    var derivatives: KnowledgeSourceDerivatives = []

    private var content: ContentSummary { entry.content }

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
        if let status = entry.statusText {
            parts.append(status.uppercased())
        }
        if detail != "SAVED" {
            parts.append(detail)
        }
        if entry.activityDate != .distantPast {
            parts.append(ContentTimestampFormatter.compactRelativeText(from: entry.activityDate).uppercased())
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

                HStack(spacing: 6) {
                    Text(kickerText)
                        .kicker(color: .onSurfaceTertiary)
                        .lineLimit(1)
                    derivativeMarkers
                }
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

    @ViewBuilder
    private var derivativeMarkers: some View {
        let actions = derivatives.actions
        if !actions.isEmpty {
            HStack(spacing: 5) {
                ForEach(actions, id: \.self) { action in
                    Image(systemName: action.systemImage)
                        .font(.appSymbol(size: 9, weight: .semibold))
                }
            }
            .foregroundStyle(Color.brandPrimary)
            .fixedSize()
            .accessibilityHidden(true)
        }
    }

    private var artwork: some View {
        KnowledgeTimelineArtwork(
            icon: hasStalled ? "exclamationmark.circle" : "photo",
            imageURL: artworkURL,
            isBusy: entry.isBusy,
            busyAccessibilityIdentifier: "knowledge.saved.\(content.id).preparing"
        )
    }
}

private struct KnowledgePreparationStatusSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    let content: ContentSummary
    let onRefresh: () -> Void
    let onReprocess: () async -> Bool
    let onRemove: () -> Void

    @State private var browserDestination: BrowserDestination?
    @State private var isReprocessing = false
    @State private var reprocessFailed = false

    private var isStalled: Bool {
        content.hasStalledKnowledgePreparation
    }

    private var canReprocess: Bool {
        isStalled || content.savedLibraryItemState == .unavailable
    }

    private var title: String {
        if isStalled {
            return "Preparation stalled"
        }
        switch content.savedLibraryItemState {
        case .processing: return "Still preparing"
        case .unavailable: return "Couldn't prepare"
        case .ready: return "Ready to read"
        }
    }

    private var message: String {
        if isStalled {
            return "This save has been preparing for over a day. Reprocess to start it over."
        }
        switch content.savedLibraryItemState {
        case .processing:
            return "This usually takes a minute or two. You can read the original in the meantime."
        case .unavailable:
            return "Reprocess to run the full preparation again, or read the original source."
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
        VStack(spacing: 0) {
            MiniSheetHeader(
                title: title,
                titleAccessibilityIdentifier: "knowledge.status.screen",
                dismiss: { dismiss() }
            )

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(content.displayTitle)
                            .font(.modalBody.weight(.semibold))
                            .foregroundStyle(Color.onSurface)
                            .lineLimit(2)

                        Text(message)
                            .font(.modalSupporting)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .fixedSize(horizontal: false, vertical: true)

                        if reprocessFailed {
                            Text("Couldn't restart preparation. Try again.")
                                .font(.modalSupporting)
                                .foregroundStyle(Color.statusDestructive)
                                .accessibilityIdentifier("knowledge.status.reprocess_error")
                        }
                    }

                    VStack(spacing: 10) {
                        primaryAction

                        if let originalURL {
                            MiniSheetOptionRow(
                                icon: "safari",
                                title: "Open original",
                                subtitle: originalURL.host() ?? originalURL.absoluteString,
                                accessibilityIdentifier: "knowledge.status.open_original"
                            ) {
                                browserDestination = BrowserDestination(url: originalURL)
                            }
                        }

                        MiniSheetOptionRow(
                            icon: "bookmark.slash",
                            iconColor: .statusDestructive,
                            title: "Remove from Knowledge",
                            subtitle: "Delete this save",
                            disabled: isReprocessing,
                            accessibilityIdentifier: "knowledge.status.remove"
                        ) {
                            onRemove()
                            dismiss()
                        }
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.bottom, 24)
            }
        }
        .frame(maxHeight: .infinity, alignment: .top)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .presentationDetents(dynamicTypeSize.isAccessibilitySize ? [.large] : [.medium, .large])
        .presentationContentInteraction(.scrolls)
        .presentationDragIndicator(.hidden)
        .presentationCornerRadius(24)
        .sheet(item: $browserDestination) { destination in
            SafariView(url: destination.url)
        }
    }

    @ViewBuilder
    private var primaryAction: some View {
        if canReprocess {
            MiniSheetOptionRow(
                icon: "arrow.clockwise",
                title: isReprocessing ? "Reprocessing…" : "Reprocess",
                subtitle: "Run the full preparation again",
                disabled: isReprocessing,
                accessibilityIdentifier: "knowledge.status.reprocess"
            ) {
                Task { await reprocess() }
            }
        } else {
            MiniSheetOptionRow(
                icon: "arrow.triangle.2.circlepath",
                title: "Check progress",
                subtitle: "Refresh this save's status",
                accessibilityIdentifier: "knowledge.status.refresh"
            ) {
                onRefresh()
                dismiss()
            }
        }
    }

    private func reprocess() async {
        isReprocessing = true
        reprocessFailed = false
        let succeeded = await onReprocess()
        isReprocessing = false
        if succeeded {
            dismiss()
        } else {
            reprocessFailed = true
        }
    }
}
