//
//  ChatSessionCard.swift
//  newsly
//

import SwiftUI

struct ChatSessionCard: View {
    let session: ChatSessionSummary

    /// Whether this session was recently active (within last 5 minutes)
    private var isRecentlyActive: Bool {
        guard let date = session.lastActivityDate else { return false }
        return Date().timeIntervalSince(date) < 300
    }

    private enum BadgeStyle {
        case thinking
        case ready
        case none
    }

    private var badgeStyle: BadgeStyle {
        if session.isProcessing { return .thinking }
        if !session.isProcessing && session.hasAnyMessages && isRecentlyActive { return .ready }
        return .none
    }

    private var relativeTimeLabel: String? {
        guard let date = session.lastActivityDate else { return nil }
        return ContentTimestampFormatter.compactRelativeText(from: date).uppercased()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Header row: title + badge + timestamp + arrow
            HStack(alignment: .top, spacing: 8) {
                Text(session.displayTitle)
                    .font(.terracottaHeadlineSmall)
                    .foregroundColor(.onSurface)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)

                Spacer(minLength: 8)

                statusBadge

                if badgeStyle == .none, let relativeTimeLabel {
                    Text(relativeTimeLabel)
                        .kicker()
                        .padding(.top, 5)
                }

                Image(systemName: "arrow.right")
                    .font(.appSymbol(size: 12, weight: .medium))
                    .foregroundColor(.onSurfaceSecondary)
                    .padding(.top, 4)
            }

            // Preview row
            previewRow
        }
        .padding(14)
        .background(Color.surfaceSecondary)
        .clipShape(RoundedRectangle(cornerRadius: CornerRadius.card, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: CornerRadius.card, style: .continuous)
                .stroke(Color.outlineVariant.opacity(0.3), lineWidth: 1)
        )
    }

    @ViewBuilder
    private var statusBadge: some View {
        switch badgeStyle {
        case .thinking:
            HStack(spacing: 4) {
                ProgressView()
                    .scaleEffect(0.5)
                Text("THINKING")
                    .font(.terracottaLabelSmall)
                    .tracking(0.5)
            }
            .foregroundColor(.onSurfaceSecondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Color.surfaceContainer)
            .cornerRadius(4)

        case .ready:
            Text("READY")
                .font(.terracottaLabelSmall)
                .tracking(0.5)
                .foregroundColor(.terracottaPrimary)
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(Color.terracottaPrimary.opacity(0.1))
                .cornerRadius(4)

        case .none:
            EmptyView()
        }
    }

    @ViewBuilder
    private var previewRow: some View {
        if let preview = session.lastMessagePreview.flatMap(plainTextPreview), !preview.isEmpty {
            let role = session.lastMessageRole ?? "assistant"

            (role == "user"
                ? Text("You: ").foregroundColor(.onSurface).fontWeight(.medium) + Text(preview)
                : Text(preview))
                .font(.terracottaBodyMedium)
                .foregroundColor(.onSurfaceSecondary)
                .lineLimit(2)
        } else if session.isEmptyKnowledgeSave, let summary = session.articleSummary, !summary.isEmpty {
            Text(plainTextPreview(summary))
                .font(.terracottaBodyMedium)
                .foregroundColor(.onSurfaceSecondary)
                .lineLimit(2)
        } else if let subtitle = session.displaySubtitle {
            Text(subtitle)
                .font(.terracottaBodyMedium)
                .foregroundColor(.onSurfaceSecondary)
                .lineLimit(2)
        }
    }

    /// Flatten a markdown-ish message excerpt into single-line plain text for card previews.
    private func plainTextPreview(_ raw: String) -> String {
        var text = raw
        text = text.replacingOccurrences(of: "```", with: " ")
        text = text.replacingOccurrences(
            of: #"\[([^\]]*)\]\([^)]*\)"#,
            with: "$1",
            options: .regularExpression
        )
        text = text.replacingOccurrences(
            of: #"(?m)^\s{0,3}(#{1,6}(\s+|$)|>\s+|[-*+]\s+|\d+\.\s+)"#,
            with: "",
            options: .regularExpression
        )
        for marker in ["**", "__", "`"] {
            text = text.replacingOccurrences(of: marker, with: "")
        }
        text = text.replacingOccurrences(
            of: #"\s+"#,
            with: " ",
            options: .regularExpression
        )
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
