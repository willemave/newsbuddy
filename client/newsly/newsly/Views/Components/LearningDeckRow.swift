//
//  LearningDeckRow.swift
//  newsly
//

import SwiftUI

struct LearningDeckRow: View {
    let deck: LearningDeck
    let isBusy: Bool
    let open: () -> Void
    let openNotes: () -> Void
    let toggleShare: () -> Void
    let retry: () -> Void
    let delete: () -> Void

    private var isFailedOrCancelled: Bool {
        switch deck.status {
        case .failed, .cancelled:
            return true
        default:
            return false
        }
    }

    // The status pill is the single status channel; the icon tile stays neutral.
    private var statusColor: Color {
        if deck.viewerAvailable {
            return .brandPrimary
        }
        switch deck.status {
        case .failed:
            return .statusDestructive
        default:
            return .onSurfaceSecondary
        }
    }

    private var iconName: String {
        switch deck.status {
        case .failed:
            return "exclamationmark.triangle"
        case .cancelled:
            return "slash.circle"
        default:
            return "rectangle.stack"
        }
    }

    private var iconColor: Color {
        deck.status == .failed ? .statusDestructive : .onSurfaceSecondary
    }

    private var noteColor: Color {
        isFailedOrCancelled ? .onSurfaceSecondary : .onSurfaceTertiary
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

    private var metaCaption: String {
        [subtitle, sourceTypeLabel, updatedText]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
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

    private var updatedText: String? {
        let rawTimestamp = deck.updatedAt ?? deck.latestRun?.updatedAt ?? deck.createdAt
        return ContentTimestampFormatter.text(from: rawTimestamp, style: .compactRelative)
    }

    var body: some View {
        HStack(spacing: 8) {
            Button(action: open) {
                cardContent
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Open \(deck.displayTitle)")
            .accessibilityIdentifier("learning_deck.row.\(deck.id).open")

            trailingMenu
        }
        .padding(DeckRowMetrics.contentPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .learningDeckRowSurface()
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, DeckRowMetrics.rowVerticalPadding)
    }

    private var cardContent: some View {
        HStack(alignment: .top, spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: DeckRowMetrics.iconRadius, style: .continuous)
                    .fill(Color.surfaceSecondary)
                Image(systemName: iconName)
                    .font(.appSymbol(size: 16, weight: .semibold))
                    .foregroundStyle(iconColor)
            }
            .frame(width: DeckRowMetrics.iconSize, height: DeckRowMetrics.iconSize)
            .learningDeckIconSurface()

            VStack(alignment: .leading, spacing: 3) {
                Text(deck.displayTitle)
                    .font(.terracottaBodyMedium.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(alignment: .firstTextBaseline, spacing: 5) {
                    Text(metaCaption)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)

                    Image(systemName: deck.shareEnabled ? "link" : "lock.fill")
                        .font(.appSymbol(size: 9, weight: .semibold))
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .accessibilityLabel(deck.shareEnabled ? "Shared" : "Private")
                }

                if let visibleNote {
                    Text(visibleNote)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(noteColor)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Spacer(minLength: 8)

            trailingStatus
        }
    }

    @ViewBuilder
    private var trailingStatus: some View {
        if isBusy {
            ProgressView()
                .controlSize(.small)
        } else {
            Text(deck.statusLabel.uppercased())
                .font(.terracottaCategoryPill)
                .foregroundStyle(statusColor)
                .contentTransition(.opacity)
                .padding(.horizontal, 8)
                .padding(.vertical, 5)
                .learningDeckStatusSurface(tint: statusColor)
                .animation(.easeInOut(duration: 0.25), value: deck.statusLabel)
        }
    }

    private var trailingMenu: some View {
        Menu {
            if deck.viewerAvailable {
                Button(action: open) {
                    Label("Open", systemImage: "play.rectangle")
                }
                .accessibilityIdentifier("learning_deck.row.\(deck.id).open_menu")
            }

            if isFailedOrCancelled {
                Button(action: retry) {
                    Label("Try again", systemImage: "arrow.clockwise")
                }
                .accessibilityIdentifier("learning_deck.row.\(deck.id).retry")
            }

            if deck.sourceNotesAvailable {
                Button(action: openNotes) {
                    Label("Notes", systemImage: "doc.text.magnifyingglass")
                }
                .accessibilityIdentifier("learning_deck.row.\(deck.id).notes")
            }

            Button(action: toggleShare) {
                Label(
                    deck.shareEnabled ? "Unshare" : "Share",
                    systemImage: deck.shareEnabled ? "link.badge.minus" : "link"
                )
            }
            .accessibilityIdentifier("learning_deck.row.\(deck.id).\(deck.shareEnabled ? "unshare" : "share")")

            Button(role: .destructive, action: delete) {
                Label("Delete", systemImage: "trash")
            }
            .accessibilityIdentifier("learning_deck.row.\(deck.id).delete")
        } label: {
            Image(systemName: "ellipsis")
                .font(.appSymbol(size: 16, weight: .semibold))
                .foregroundStyle(Color.onSurfaceSecondary)
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .disabled(isBusy)
        .accessibilityLabel("Deck actions")
        .accessibilityIdentifier("learning_deck.row.\(deck.id).menu")
    }
}
