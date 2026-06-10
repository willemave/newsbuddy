//
//  CustomNarrationPickerSheet.swift
//  newsly
//

import SwiftUI

struct CustomNarrationPickerSheet: View {
    let currentItems: [ContentSummary]
    let currentFastReadItems: [ContentSummary]
    let isCreating: Bool
    let onCreate: (CustomNarrationSourceSelection) async -> Bool

    @Environment(\.dismiss) private var dismiss
    @State private var savedItems: [ContentSummary] = []
    @State private var loadedFastReadItems: [ContentSummary] = []
    @State private var selectedContentIds: Set<Int> = []
    @State private var selectedNewsItemIds: Set<Int> = []
    @State private var markLongReadsAsReadOnPlay = false
    @State private var isLoadingSources = false
    @State private var savedLoadError: String?
    @State private var fastReadLoadError: String?

    private let maxSelectionCount = 12

    private var longFormItems: [ContentSummary] {
        deduplicatedItems(from: currentItems + savedItems)
            .filter(supportsLongFormCustomNarration)
    }

    private var fastReadItems: [ContentSummary] {
        deduplicatedItems(from: currentFastReadItems + loadedFastReadItems)
            .filter(supportsFastReadCustomNarration)
    }

    private var selectedCount: Int {
        selectedContentIds.count + selectedNewsItemIds.count
    }

    private var hasSources: Bool {
        !longFormItems.isEmpty || !fastReadItems.isEmpty
    }

    var body: some View {
        NavigationStack {
            Group {
                if isLoadingSources && !hasSources {
                    ProgressView("Loading")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if !hasSources {
                    emptyState
                } else {
                    List {
                        errorRows

                        if !longFormItems.isEmpty {
                            Section {
                                ForEach(longFormItems) { item in
                                    pickerRow(item, kind: .longForm)
                                }
                            } header: {
                                Text("Long Read")
                            } footer: {
                                if !selectedContentIds.isEmpty {
                                    Toggle(
                                        "Mark selected Long Reads as read on play",
                                        isOn: $markLongReadsAsReadOnPlay
                                    )
                                    .font(.terracottaBodySmall)
                                    .foregroundStyle(Color.onSurfaceSecondary)
                                    .disabled(isCreating)
                                }
                            }
                        }

                        if !fastReadItems.isEmpty {
                            Section {
                                ForEach(fastReadItems) { item in
                                    pickerRow(item, kind: .fastRead)
                                }
                            } header: {
                                Text("Fast Read")
                            } footer: {
                                Text("Fast Reads mark read when the narration is played.")
                                    .font(.terracottaBodySmall)
                                    .foregroundStyle(Color.onSurfaceSecondary)
                            }
                        }

                        Section {
                            Text("\(selectedCount) of \(maxSelectionCount) selected")
                                .font(.terracottaBodySmall)
                                .foregroundStyle(Color.onSurfaceSecondary)
                        }
                    }
                    .listStyle(.plain)
                    .scrollContentBackground(.hidden)
                }
            }
            .background(Color.surfacePrimary)
            .navigationTitle("Create narration")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                    .disabled(isCreating)
                }

                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task {
                            if await onCreate(selection) {
                                dismiss()
                            }
                        }
                    } label: {
                        if isCreating {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Text("Create")
                                .fontWeight(.semibold)
                        }
                    }
                    .disabled(selectedCount == 0 || isCreating)
                }
            }
            .task {
                await loadSources()
            }
        }
    }

    @ViewBuilder
    private var errorRows: some View {
        if let savedLoadError {
            Text(savedLoadError)
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.statusDestructive)
                .appRow(.compact)
                .appListRow()
        }

        if let fastReadLoadError {
            Text(fastReadLoadError)
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.statusDestructive)
                .appRow(.compact)
                .appListRow()
        }
    }

    private var selection: CustomNarrationSourceSelection {
        CustomNarrationSourceSelection(
            contentIds: longFormItems
                .filter { selectedContentIds.contains($0.id) }
                .map(\.id),
            newsItemIds: fastReadItems
                .filter { selectedNewsItemIds.contains($0.id) }
                .map(\.id),
            markSourceContentReadOnPlay: markLongReadsAsReadOnPlay
        )
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "waveform")
                .font(.appSymbol(size: 34, weight: .medium))
                .foregroundStyle(Color.onSurfaceSecondary)

            Text("No narration sources")
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(Color.onSurface)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }

    private func pickerRow(_ item: ContentSummary, kind: NarrationSourceKind) -> some View {
        Button {
            toggleSelection(item, kind: kind)
        } label: {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.displayTitle)
                        .font(.terracottaBodyLarge.weight(.semibold))
                        .foregroundStyle(Color.onSurface)
                        .lineLimit(2)

                    Text(rowMetadata(for: item, kind: kind).joined(separator: " • "))
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(1)
                }

                Spacer()

                Image(systemName: isSelected(item, kind: kind) ? "checkmark.circle.fill" : "circle")
                    .font(.appSymbol(size: 22, weight: .semibold))
                    .foregroundStyle(
                        isSelected(item, kind: kind)
                            ? Color.terracottaPrimary
                            : Color.onSurfaceSecondary.opacity(0.55)
                    )
            }
            .appRow()
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isCreating || isSelectionLimitReached(for: item, kind: kind))
        .appListRow()
    }

    private func toggleSelection(_ item: ContentSummary, kind: NarrationSourceKind) {
        switch kind {
        case .longForm:
            if selectedContentIds.contains(item.id) {
                selectedContentIds.remove(item.id)
                if selectedContentIds.isEmpty {
                    markLongReadsAsReadOnPlay = false
                }
            } else if selectedCount < maxSelectionCount {
                selectedContentIds.insert(item.id)
            }
        case .fastRead:
            if selectedNewsItemIds.contains(item.id) {
                selectedNewsItemIds.remove(item.id)
            } else if selectedCount < maxSelectionCount {
                selectedNewsItemIds.insert(item.id)
            }
        }
    }

    private func isSelected(_ item: ContentSummary, kind: NarrationSourceKind) -> Bool {
        switch kind {
        case .longForm:
            return selectedContentIds.contains(item.id)
        case .fastRead:
            return selectedNewsItemIds.contains(item.id)
        }
    }

    private func isSelectionLimitReached(
        for item: ContentSummary,
        kind: NarrationSourceKind
    ) -> Bool {
        !isSelected(item, kind: kind) && selectedCount >= maxSelectionCount
    }

    private func loadSources() async {
        guard !isLoadingSources else { return }
        isLoadingSources = true
        defer { isLoadingSources = false }

        await loadSavedItems()
        await loadFastReadItems()
    }

    private func loadSavedItems() async {
        do {
            let response = try await ContentService.shared.fetchKnowledgeLibrary(limit: 50)
            savedItems = response.contents
            savedLoadError = nil
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            savedLoadError = "Saved items could not be loaded."
        }
    }

    private func loadFastReadItems() async {
        do {
            let response = try await ContentService.shared.fetchNewsItemList(
                readFilter: "unread",
                limit: 50
            )
            loadedFastReadItems = response.contents
            fastReadLoadError = nil
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            fastReadLoadError = "Fast Reads could not be loaded."
        }
    }

    private func supportsLongFormCustomNarration(_ item: ContentSummary) -> Bool {
        guard let type = item.apiContentType else { return false }
        return type == .article || type == .podcast
    }

    private func supportsFastReadCustomNarration(_ item: ContentSummary) -> Bool {
        item.apiContentType == .news
    }

    private func rowMetadata(for item: ContentSummary, kind: NarrationSourceKind) -> [String] {
        var labels: [String] = []
        switch kind {
        case .longForm:
            labels.append(customNarrationTypeLabel(for: item))
        case .fastRead:
            labels.append("Fast Read")
        }

        if let sourceName = customNarrationSourceLabel(for: item) {
            labels.append(sourceName)
        }

        if kind == .longForm && !currentItems.contains(where: { $0.id == item.id }) {
            labels.append("Saved")
        }

        if kind == .fastRead, let time = item.relativeTimeDisplay {
            labels.append(time)
        }

        return labels
    }

    private func customNarrationTypeLabel(for item: ContentSummary) -> String {
        switch item.apiContentType {
        case .podcast:
            return "Podcast"
        case .article:
            return "Blog"
        default:
            return "Source"
        }
    }

    private func customNarrationSourceLabel(for item: ContentSummary) -> String? {
        for candidate in [item.source, item.savedSource, item.platform] {
            if let normalized = normalizedMetadataLabel(candidate) {
                return normalized
            }
        }

        guard
            let host = URL(string: item.url)?.host?
                .replacingOccurrences(of: "www.", with: "")
        else {
            return nil
        }
        return normalizedMetadataLabel(host)
    }

    private func normalizedMetadataLabel(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func deduplicatedItems(from items: [ContentSummary]) -> [ContentSummary] {
        var seenIds: Set<Int> = []
        var merged: [ContentSummary] = []
        for item in items {
            guard !seenIds.contains(item.id) else { continue }
            seenIds.insert(item.id)
            merged.append(item)
        }
        return merged
    }
}

private enum NarrationSourceKind {
    case longForm
    case fastRead
}
