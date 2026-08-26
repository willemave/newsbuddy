//
//  KnowledgeTimelineRows.swift
//  newsly
//

import SwiftUI

struct KnowledgeChatRow: View {
    let session: ChatSessionSummary
    let activityDate: Date
    let preview: String?

    private var isPreparing: Bool {
        session.isPreparingChat || session.isProcessing
    }

    var body: some View {
        KnowledgeTimelineRow(
            icon: "bubble.left.and.bubble.right",
            imageURL: (session.articleThumbnailUrl ?? session.articleImageUrl)
                .flatMap(ServerImageURL.resolve),
            isBusy: isPreparing,
            busyAccessibilityIdentifier: isPreparing
                ? "knowledge.chat.\(session.id).preparing"
                : nil,
            title: session.displayTitle,
            subtitle: preview,
            kicker: "CHAT · \(ContentTimestampFormatter.compactRelativeText(from: activityDate).uppercased())"
        ) {
            Image(systemName: "chevron.right")
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.onSurfaceTertiary)
        }
    }
}

struct KnowledgeDayDelimiter: View {
    let title: String

    var body: some View {
        HStack(spacing: 10) {
            Rectangle().fill(Color.outlineVariant).frame(height: 0.5)
            Text(title).kicker(color: .onSurfaceTertiary).fixedSize()
            Rectangle().fill(Color.outlineVariant).frame(height: 0.5)
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 10)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(title.capitalized)
    }
}

struct KnowledgeDeckTimelineRow: View {
    let deck: LearningDeck
    let activityDate: Date

    // Status only earns kicker space when the deck is not simply ready.
    private var kicker: String {
        var parts = ["DECK"]
        if deck.statusLabel != "Ready" {
            parts.append(deck.statusLabel.uppercased())
        }
        parts.append(ContentTimestampFormatter.compactRelativeText(from: activityDate).uppercased())
        return parts.joined(separator: " · ")
    }

    var body: some View {
        KnowledgeTimelineRow(
            icon: "rectangle.on.rectangle",
            isBusy: deck.hasActiveLatestRun,
            busyAccessibilityIdentifier: deck.hasActiveLatestRun
                ? "knowledge.deck.\(deck.id).preparing"
                : nil,
            title: deck.displayTitle,
            subtitle: deck.timelineSubtitle,
            kicker: kicker
        ) {
            Image(systemName: "chevron.right")
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.onSurfaceTertiary)
        }
    }
}

struct KnowledgeNarrationRow: View {
    let episode: AudioEpisode
    let activityDate: Date
    let subtitle: String
    let isPlaying: Bool

    // Match the other timeline kickers: status only when abnormal, then time.
    private var kicker: String {
        var parts = ["AUDIO"]
        if episode.isFailed {
            parts.append("FAILED")
        } else if episode.isGenerating {
            parts.append("PREPARING")
        }
        parts.append(ContentTimestampFormatter.compactRelativeText(from: activityDate).uppercased())
        return parts.joined(separator: " · ")
    }

    var body: some View {
        KnowledgeTimelineRow(
            icon: "waveform",
            title: episode.title,
            subtitle: subtitle,
            kicker: kicker
        ) {
            Image(
                systemName: episode.isFailed
                    ? "arrow.clockwise"
                    : (isPlaying ? "pause.fill" : "play.fill")
            )
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.brandPrimary)
                .frame(width: 30, height: 30)
                .background(Color.surfaceSecondary)
                .clipShape(Circle())
        }
    }
}

struct KnowledgeTimelineInlineError: View {
    let message: String
    let actionTitle: String
    let accessibilityIdentifier: String
    let action: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.circle")
                .foregroundStyle(Color.onSurfaceSecondary)
                .accessibilityHidden(true)
            Text(message)
                .font(.terracottaBodyMedium)
                .foregroundStyle(Color.onSurfaceSecondary)
            Spacer(minLength: 8)
            Button(actionTitle, action: action)
                .buttonStyle(.bordered)
                .accessibilityIdentifier("\(accessibilityIdentifier).action")
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 10)
        .accessibilityIdentifier(accessibilityIdentifier)
    }
}

struct KnowledgeTimelineRow<Accessory: View>: View {
    let icon: String
    var imageURL: URL? = nil
    var isBusy = false
    var busyAccessibilityIdentifier: String?
    let title: String
    let subtitle: String?
    let kicker: String
    @ViewBuilder var accessory: () -> Accessory

    var body: some View {
        HStack(spacing: 12) {
            KnowledgeTimelineArtwork(
                icon: icon,
                imageURL: imageURL,
                isBusy: isBusy,
                busyAccessibilityIdentifier: busyAccessibilityIdentifier
            )

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(1)
                    .truncationMode(.tail)

                if let subtitle, !subtitle.isEmpty, subtitle != title {
                    Text(subtitle)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }

                Text(kicker)
                    .kicker(color: .onSurfaceTertiary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            accessory()
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 8)
        .contentShape(Rectangle())
    }
}

/// Hairline between rows of the same day group, aligned with the text column.
/// `Divider` renders as a stray vertical tick inside these list rows, so draw
/// the rule explicitly like `KnowledgeDayDelimiter` does.
struct KnowledgeTimelineRowDivider: View {
    var body: some View {
        Rectangle()
            .fill(Color.outlineVariant)
            .frame(height: 0.5)
            .padding(.leading, Spacing.appHorizontalMargin + KnowledgeTimelineArtwork.size + 12)
            .padding(.trailing, Spacing.appHorizontalMargin)
    }
}

struct KnowledgeTimelineArtwork: View {
    let icon: String
    var imageURL: URL? = nil
    var isBusy = false
    var busyAccessibilityIdentifier: String?

    static let size: CGFloat = 40
    private var size: CGFloat { Self.size }

    var body: some View {
        ZStack {
            Color.surfaceSecondary
            if let imageURL {
                CachedAsyncImage(url: imageURL, targetSize: CGSize(width: size, height: size)) { image in
                    image.resizable().aspectRatio(contentMode: .fill)
                } placeholder: {
                    ProgressView().controlSize(.small)
                }
            } else {
                Image(systemName: icon)
                    .font(.appSymbol(size: 16))
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.outlineVariant.opacity(0.45), lineWidth: 0.5)
        }
        .overlay(alignment: .topTrailing) {
            if isBusy {
                PreparingActivityDot()
                    .padding(2.5)
                    .background(Color.surfacePrimary, in: Circle())
                    .offset(x: 3, y: -3)
                    .accessibilityIdentifier(ifPresent: busyAccessibilityIdentifier)
            }
        }
    }
}

/// Breathing dot standing in for a spinner on rows still being prepared.
/// A `.mini` `ProgressView` renders as a low-resolution aperture at this size
/// and read as a rendering artifact sitting next to the timestamp.
struct PreparingActivityDot: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isDimmed = false

    var body: some View {
        Circle()
            .fill(Color.brandPrimary)
            .frame(width: 5, height: 5)
            .opacity(isDimmed ? 0.3 : 1)
            .animation(reduceMotion ? nil : AppMotion.chatStatusPulse, value: isDimmed)
            .onAppear { isDimmed = !reduceMotion }
            .onChange(of: reduceMotion) { _, newValue in isDimmed = !newValue }
            .accessibilityHidden(true)
    }
}
