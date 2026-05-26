//
//  CustomNarrationPickerSheet.swift
//  newsly
//

import SwiftUI

struct CustomNarrationPickerSheet: View {
    let currentItems: [ContentSummary]
    let isCreating: Bool
    let onCreate: ([ContentSummary]) async -> Bool

    @Environment(\.dismiss) private var dismiss
    @State private var savedItems: [ContentSummary] = []
    @State private var selectedIds: Set<Int> = []
    @State private var isLoadingSavedItems = false
    @State private var loadError: String?

    private let maxSelectionCount = 12

    private var eligibleItems: [ContentSummary] {
        var seenIds: Set<Int> = []
        var merged: [ContentSummary] = []
        for item in currentItems + savedItems {
            guard supportsCustomNarration(item), !seenIds.contains(item.id) else { continue }
            seenIds.insert(item.id)
            merged.append(item)
        }
        return merged
    }

    private var selectedItems: [ContentSummary] {
        eligibleItems.filter { selectedIds.contains($0.id) }
    }

    var body: some View {
        NavigationStack {
            Group {
                if isLoadingSavedItems && eligibleItems.isEmpty {
                    ProgressView("Loading")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if eligibleItems.isEmpty {
                    emptyState
                } else {
                    List {
                        if let loadError {
                            Text(loadError)
                                .font(.terracottaBodySmall)
                                .foregroundStyle(Color.statusDestructive)
                                .appRow(.compact)
                                .appListRow()
                        }

                        Section {
                            ForEach(eligibleItems) { item in
                                pickerRow(item)
                            }
                        } footer: {
                            Text("\(selectedIds.count) of \(maxSelectionCount) selected")
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
                            if await onCreate(selectedItems) {
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
                    .disabled(selectedIds.isEmpty || isCreating)
                }
            }
            .task {
                await loadSavedItems()
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "waveform")
                .font(.system(size: 34, weight: .medium))
                .foregroundStyle(loadError == nil ? Color.onSurfaceSecondary : Color.statusDestructive)

            Text(loadError ?? "No articles or podcasts")
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(loadError == nil ? Color.onSurface : Color.statusDestructive)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }

    private func pickerRow(_ item: ContentSummary) -> some View {
        Button {
            toggleSelection(item)
        } label: {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.displayTitle)
                        .font(.terracottaBodyLarge.weight(.semibold))
                        .foregroundStyle(Color.onSurface)
                        .lineLimit(2)

                    Text(rowMetadata(for: item).joined(separator: " • "))
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .lineLimit(1)
                }

                Spacer()

                Image(systemName: selectedIds.contains(item.id) ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(
                        selectedIds.contains(item.id)
                            ? Color.terracottaPrimary
                            : Color.onSurfaceSecondary.opacity(0.55)
                    )
            }
            .appRow()
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isCreating || isSelectionLimitReached(for: item))
        .appListRow()
    }

    private func toggleSelection(_ item: ContentSummary) {
        if selectedIds.contains(item.id) {
            selectedIds.remove(item.id)
        } else if selectedIds.count < maxSelectionCount {
            selectedIds.insert(item.id)
        }
    }

    private func isSelectionLimitReached(for item: ContentSummary) -> Bool {
        !selectedIds.contains(item.id) && selectedIds.count >= maxSelectionCount
    }

    private func loadSavedItems() async {
        guard !isLoadingSavedItems else { return }
        isLoadingSavedItems = true
        defer { isLoadingSavedItems = false }

        do {
            let response = try await ContentService.shared.fetchKnowledgeLibrary(limit: 50)
            savedItems = response.contents
            loadError = nil
        } catch {
            loadError = "Saved items could not be loaded."
        }
    }

    private func supportsCustomNarration(_ item: ContentSummary) -> Bool {
        guard let type = item.contentTypeEnum else { return false }
        return type == .article || type == .podcast
    }

    private func rowMetadata(for item: ContentSummary) -> [String] {
        var labels: [String] = []
        labels.append(customNarrationTypeLabel(for: item))

        if let sourceName = customNarrationSourceLabel(for: item) {
            labels.append(sourceName)
        }

        if !currentItems.contains(where: { $0.id == item.id }) {
            labels.append("Saved")
        }

        return labels
    }

    private func customNarrationTypeLabel(for item: ContentSummary) -> String {
        switch item.contentTypeEnum {
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
}
