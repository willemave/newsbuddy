//
//  LearningDeckRow.swift
//  newsly
//

import SwiftUI

struct LearningDeckLoadingRow: View {
    var body: some View {
        HStack(spacing: 10) {
            ProgressView()
                .controlSize(.small)
            Text("Loading decks")
                .font(.terracottaBodyMedium)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .padding(.vertical, 10)
    }
}

struct LearningDeckEmptyRow: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("No decks yet")
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(Color.onSurface)
            Text("Created Learning Decks will show up here.")
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 10)
    }
}

struct LearningDeckRow: View {
    let deck: LearningDeck
    let isBusy: Bool
    let open: () -> Void
    let openNotes: () -> Void
    let toggleShare: () -> Void
    let delete: () -> Void

    private var statusColor: Color {
        if deck.viewerAvailable {
            return .brandPrimary
        }
        switch deck.status {
        case .failed, .cancelled:
            return .statusDestructive
        case .queued, .preparing:
            return .onSurfaceSecondary
        case .generating, .validating, .publishing, .completed, .ready, .unknown, nil:
            return .onSurfaceSecondary
        }
    }

    private var visibleNote: String? {
        guard let latestNote = deck.latestNote else { return nil }
        return deck.viewerAvailable ? nil : latestNote
    }

    private var subtitle: String {
        if let sourceTitle = nonEmptyTrimmed(deck.sourceTitle),
           sourceTitle != deck.displayTitle {
            return sourceTitle
        }
        if let sourceURL = deck.sourceURL,
           let host = URL(string: sourceURL)?.host {
            return host
        }
        return deck.sourceKind == .githubRepo ? "GitHub repository" : "Saved source"
    }

    private var summaryLine: String {
        [subtitle, metadataLine]
            .filter { !$0.isEmpty }
            .joined(separator: " / ")
    }

    private var metadataLine: String {
        [
            sourceTypeLabel,
            updatedLabel,
            deck.shareEnabled ? "Shared" : "Private",
        ]
        .compactMap { $0 }
        .joined(separator: " / ")
    }

    private var sourceTypeLabel: String {
        if deck.sourceKind == .githubRepo {
            return "GitHub repo"
        }

        if let rawType = nonEmptyTrimmed(deck.sourceMetadata["content_type"]?.value as? String),
           rawType.lowercased() != "unknown" {
            return rawType
                .split(separator: "_")
                .map { $0.capitalized }
                .joined(separator: " ")
        }

        if let sourceURL = deck.sourceURL,
           URL(string: sourceURL)?.host?.contains("arxiv.org") == true {
            return "Paper"
        }

        return "Content"
    }

    private var updatedLabel: String? {
        let rawTimestamp = deck.updatedAt ?? deck.latestRun?.updatedAt ?? deck.createdAt
        guard let text = ContentTimestampFormatter.text(
            from: rawTimestamp,
            style: .compactRelative
        ) else {
            return nil
        }
        return "Updated \(text)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "rectangle.stack")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(statusColor)
                    .frame(width: 34, height: 34)
                    .background(statusColor.opacity(0.13))
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))

                VStack(alignment: .leading, spacing: 2) {
                    Text(deck.displayTitle)
                        .font(.terracottaBodyMedium.weight(.semibold))
                        .foregroundStyle(Color.onSurface)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)

                    Text(summaryLine)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .truncationMode(.tail)

                    if let visibleNote {
                        Text(visibleNote)
                            .font(.terracottaBodySmall)
                            .foregroundStyle(Color.onSurfaceTertiary)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                Spacer(minLength: 0)

                if isBusy {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Text(deck.statusLabel.uppercased())
                        .font(.terracottaCategoryPill)
                        .foregroundStyle(statusColor)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 5)
                        .background(statusColor.opacity(0.11), in: Capsule())
                }
            }

            HStack(spacing: 8) {
                LearningDeckActionButton(
                    title: deck.viewerAvailable ? "Open" : "Refresh",
                    systemImage: deck.viewerAvailable ? "play.rectangle" : "arrow.clockwise",
                    disabled: isBusy,
                    action: open
                )

                if deck.sourceNotesAvailable {
                    LearningDeckActionButton(
                        title: "Notes",
                        systemImage: "doc.text.magnifyingglass",
                        disabled: isBusy,
                        action: openNotes
                    )
                }

                LearningDeckActionButton(
                    title: deck.shareEnabled ? "Unshare" : "Share",
                    systemImage: deck.shareEnabled ? "link.badge.minus" : "link",
                    disabled: isBusy,
                    action: toggleShare
                )

                Spacer(minLength: 0)

                Button(role: .destructive, action: delete) {
                    ZStack {
                        Color.clear
                        Image(systemName: "trash")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .accessibilityHidden(true)
                    }
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
                }
                .disabled(isBusy)
                .buttonStyle(.plain)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Delete Learning Deck")
                .accessibilityAddTraits(.isButton)
            }
        }
        .padding(.horizontal, Spacing.screenHorizontal)
        .padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.surfacePrimary)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(Color.outlineVariant.opacity(0.28))
                .frame(height: 0.5)
                .padding(.leading, Spacing.screenHorizontal + 44)
                .padding(.trailing, Spacing.screenHorizontal)
        }
    }
}

private func nonEmptyTrimmed(_ value: String?) -> String? {
    guard let value else { return nil }
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.isEmpty ? nil : trimmed
}

private struct LearningDeckActionButton: View {
    let title: String
    let systemImage: String
    let disabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Label(title, systemImage: systemImage)
                .font(.terracottaBodySmall.weight(.semibold))
                .foregroundStyle(disabled ? Color.onSurfaceSecondary : Color.terracottaPrimary)
                .lineLimit(1)
                .minimumScaleFactor(0.82)
                .padding(.horizontal, 8)
                .padding(.vertical, 5)
                .frame(minHeight: 44)
                .background(
                    disabled
                        ? Color.surfaceSecondary.opacity(0.3)
                        : Color.surfaceSecondary.opacity(0.62),
                    in: Capsule()
                )
        }
        .disabled(disabled)
        .buttonStyle(.plain)
    }
}
