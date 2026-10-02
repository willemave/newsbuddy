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
                            onReprocess: { await viewModel.reprocessKnowledgeItem(content.id) },
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
    let onReprocess: () async -> Bool
    let onRemove: () -> Void
    var derivatives: KnowledgeSourceDerivatives = []
    var showsSwipeHint = false
    var onSwipeHintFinished: () -> Void = {}

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var showsPreparationStatus = false
    @State private var swipeHintOffset: CGFloat = 0
    @State private var showsSwipeHintActions = false

    var body: some View {
        Button {
            guard content.savedLibraryItemState == .ready else {
                showsPreparationStatus = true
                return
            }
            onOpen()
        } label: {
            KnowledgeSavedRow(content: content, derivatives: derivatives)
        }
        .buttonStyle(.plain)
        .offset(x: swipeHintOffset)
        .background(alignment: .leading) {
            if showsSwipeHintActions {
                KnowledgeSwipeHintActions()
                    .frame(width: swipeHintOffset, alignment: .trailing)
                    .clipped()
                    .accessibilityHidden(true)
            }
        }
        .task(id: showsSwipeHint) { await playSwipeHint() }
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
                onReprocess: onReprocess,
                onRemove: onRemove
            )
        }
    }

    /// Briefly slides the row open to reveal the leading swipe actions, then closes it.
    private func playSwipeHint() async {
        guard showsSwipeHint else { return }
        let open = reduceMotion ? nil : Animation.spring(response: 0.45, dampingFraction: 0.82)
        let close = reduceMotion ? nil : Animation.spring(response: 0.5, dampingFraction: 0.9)
        do {
            try await Task.sleep(for: .milliseconds(700))
            showsSwipeHintActions = true
            withAnimation(open) { swipeHintOffset = KnowledgeSwipeHintActions.width }
            try await Task.sleep(for: .milliseconds(1_600))
        } catch {
            swipeHintOffset = 0
            showsSwipeHintActions = false
            return
        }
        withAnimation(close) {
            swipeHintOffset = 0
        } completion: {
            showsSwipeHintActions = false
        }
        onSwipeHintFinished()
    }
}

/// Static copy of the leading swipe actions, revealed by the one-time row hint.
/// Mirrors the system's round swipe buttons with captions underneath.
private struct KnowledgeSwipeHintActions: View {
    private static let actionWidth: CGFloat = 70
    static let width = actionWidth * CGFloat(KnowledgeSourceAction.leading.count)

    var body: some View {
        HStack(spacing: 0) {
            ForEach(KnowledgeSourceAction.leading, id: \.self) { action in
                VStack(spacing: 5) {
                    Image(systemName: action.systemImage)
                        .font(.appSymbol(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 58, height: 40)
                        .background(action.tint, in: Capsule())
                    Text(action.title)
                        .font(.appCaption2)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
                .frame(width: Self.actionWidth)
            }
        }
        .frame(width: Self.width)
    }
}

/// Deep dives offered from a saved Knowledge row's swipe actions.
enum KnowledgeSourceAction: Hashable {
    case chat
    case deck
    case listen
    case council

    static let leading: [Self] = [.chat, .deck, .listen]

    var title: String {
        switch self {
        case .chat: "Chat"
        case .deck: "Deck"
        case .listen: "Listen"
        case .council: "Council"
        }
    }

    var systemImage: String {
        switch self {
        case .chat: "message"
        case .deck: "rectangle.on.rectangle"
        case .listen: "headphones"
        case .council: "person.3.sequence.fill"
        }
    }

    var tint: Color {
        switch self {
        case .chat: Color.brandPrimary
        case .deck: Color.onSurfaceSecondary
        case .listen, .council: Color.onSurfaceTertiary
        }
    }
}

struct KnowledgeSavedRow: View {
    let content: ContentSummary
    var derivatives: KnowledgeSourceDerivatives = []

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

    private static let derivativeMarkerOrder: [(KnowledgeSourceDerivatives, KnowledgeSourceAction)] = [
        (.deck, .deck),
        (.chat, .chat),
        (.council, .council),
        (.narration, .listen)
    ]

    @ViewBuilder
    private var derivativeMarkers: some View {
        let icons = Self.derivativeMarkerOrder
            .filter { derivatives.contains($0.0) }
            .map(\.1.systemImage)
        if !icons.isEmpty {
            HStack(spacing: 5) {
                ForEach(icons, id: \.self) { icon in
                    Image(systemName: icon)
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
            isBusy: content.savedLibraryItemState == .processing && !hasStalled,
            busyAccessibilityIdentifier: "knowledge.saved.\(content.id).preparing"
        )
    }
}

private struct KnowledgePreparationStatusSheet: View {
    @Environment(\.dismiss) private var dismiss

    let content: ContentSummary
    let onRefresh: () -> Void
    let onReprocess: () async -> Bool
    let onRemove: () -> Void

    @State private var browserDestination: BrowserDestination?
    @State private var isReprocessing = false
    @State private var reprocessFailed = false
    @State private var sheetHeight: CGFloat = 380

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

            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(content.displayTitle)
                        .font(.terracottaHeadlineSmall)
                        .foregroundStyle(Color.onSurface)
                        .lineLimit(2)

                    Text(message)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .fixedSize(horizontal: false, vertical: true)

                    if reprocessFailed {
                        Text("Couldn't restart preparation. Try again.")
                            .font(.appCaption)
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
        .fixedSize(horizontal: false, vertical: true)
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { sheetHeight = $0 }
        .frame(maxHeight: .infinity, alignment: .top)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .presentationDetents([.height(sheetHeight)])
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

private extension ContentSummary {
    var hasStalledKnowledgePreparation: Bool {
        guard savedLibraryItemState == .processing,
              let createdAt = ContentTimestampFormatter.parse(createdAt)
        else { return false }
        return AppClock.now.timeIntervalSince(createdAt) > 24 * 60 * 60
    }
}
