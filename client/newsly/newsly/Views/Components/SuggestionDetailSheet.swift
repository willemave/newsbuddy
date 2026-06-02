//
//  SuggestionDetailSheet.swift
//  newsly
//

import SwiftUI

struct SuggestionDetailSheet: View {
    let suggestion: DiscoverySuggestion
    let onSubscribe: () -> Void
    let onAddItem: (() -> Void)?
    let onPreview: () -> Void
    let onDismiss: () -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Drag indicator
            Capsule()
                .fill(Color.outlineVariant.opacity(0.6))
                .frame(width: 36, height: 5)
                .frame(maxWidth: .infinity)
                .padding(.top, 10)
                .padding(.bottom, 20)

            // Type icon + source title
            HStack(spacing: 10) {
                Image(systemName: metadata.systemImageName)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(metadata.color)

                Text(suggestion.displayTitle)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundColor(.editorialText)
                    .lineLimit(2)
            }
            .padding(.horizontal, 20)

            // URL
            Text(formattedURL(suggestion.primaryURL))
                .font(.editorialSubMeta)
                .foregroundColor(.editorialSub)
                .lineLimit(1)
                .padding(.horizontal, 20)
                .padding(.top, 6)

            // Rationale / description
            if let rationale = suggestion.rationale, !rationale.isEmpty {
                Text(rationale)
                    .font(.editorialBody)
                    .italic()
                    .foregroundColor(.editorialSub)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 20)
                    .padding(.top, 14)
            } else if let desc = suggestion.description, !desc.isEmpty {
                Text(desc)
                    .font(.editorialBody)
                    .italic()
                    .foregroundColor(.editorialSub)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 20)
                    .padding(.top, 14)
            }

            Spacer().frame(height: 24)

            // Action buttons
            HStack(spacing: 10) {
                if suggestion.canSubscribe {
                    Button(action: {
                        onSubscribe()
                        dismiss()
                    }) {
                        Label("Subscribe", systemImage: "plus")
                            .font(.subheadline.weight(.semibold))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(metadata.color)
                }

                Button(action: {
                    onPreview()
                    dismiss()
                }) {
                    Label("Preview", systemImage: "safari")
                        .font(.subheadline.weight(.medium))
                        .frame(maxWidth: suggestion.canSubscribe ? nil : .infinity)
                        .padding(.vertical, 12)
                        .padding(.horizontal, suggestion.canSubscribe ? 16 : 0)
                }
                .buttonStyle(.bordered)

                if let onAddItem {
                    Button(action: {
                        onAddItem()
                        dismiss()
                    }) {
                        Label(suggestion.addItemLabel, systemImage: "arrow.down.circle")
                            .font(.subheadline.weight(.medium))
                            .padding(.vertical, 12)
                            .padding(.horizontal, 12)
                    }
                    .buttonStyle(.bordered)
                    .tint(Color.onSurfaceSecondary)
                }
            }
            .padding(.horizontal, 20)

            // Dismiss link
            Button(action: {
                onDismiss()
                dismiss()
            }) {
                Text("Not interested")
                    .font(.subheadline)
                    .foregroundColor(.editorialSub)
                    .frame(maxWidth: .infinity)
                    .frame(minHeight: RowMetrics.compactHeight)
            }
            .buttonStyle(.plain)
        }
        .presentationDetents([.height(320)])
        .presentationDragIndicator(.hidden)
        .accessibilityIdentifier("discovery.suggestion.sheet")
    }

    // MARK: - Type Helpers

    private var metadata: SourceVisualMetadata {
        SourceVisualMetadata.suggestionType(suggestion.suggestionType)
    }

    private func formattedURL(_ urlString: String) -> String {
        guard let url = URL(string: urlString),
              let host = url.host else {
            return urlString
        }
        return host.replacingOccurrences(of: "www.", with: "")
    }
}
