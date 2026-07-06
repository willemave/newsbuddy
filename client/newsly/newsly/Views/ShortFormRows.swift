//
//  ShortFormRows.swift
//  newsly
//

import SwiftUI

struct FeedRowButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .overlay {
                Color.onSurface.opacity(configuration.isPressed ? 0.06 : 0)
                    .allowsHitTesting(false)
            }
            .animation(AppMotion.press, value: configuration.isPressed)
    }
}

struct ShortNewsRow: View, Equatable {
    let item: ContentSummary

    @Environment(\.displayScale) private var displayScale

    static func == (lhs: ShortNewsRow, rhs: ShortNewsRow) -> Bool {
        lhs.item == rhs.item
    }

    private var titleColor: Color {
        item.isRead ? .onSurfaceSecondary : .readerBodyText
    }

    private var titleFont: Font {
        .appSerif(size: 18, relativeTo: .headline, weight: .medium)
    }

    private var metadataColor: Color {
        Color.platformLabel
    }

    private var metadataParts: [String] {
        var parts: [String] = []
        if let source = FastReadPresentation.sourceLabel(for: item) {
            parts.append(source)
        }
        if let time = item.relativeTimeDisplay {
            parts.append(time.uppercased())
        }
        return parts
    }

    var body: some View {
        let metadata = metadataParts

        VStack(alignment: .leading, spacing: 7) {
            FeedListText(
                item.displayTitle,
                textColor: titleColor,
                font: titleFont,
                lineLimit: 3
            )

            if !metadata.isEmpty || item.commentCountDisplay != nil {
                metadataRow(parts: metadata)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 14)
        .background(Color.surfacePrimary)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(Color.borderSubtle.opacity(0.48))
                .frame(height: 1 / displayScale)
                .padding(.horizontal, Spacing.appHorizontalMargin)
        }
        .accessibilityElement(children: .combine)
    }

    private func metadataRow(parts metadataParts: [String]) -> some View {
        HStack(spacing: 6) {
            if !metadataParts.isEmpty {
                Text(metadataParts.joined(separator: "  •  "))
                    .kicker(color: metadataColor)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }

            if let comments = item.commentCountDisplay {
                if !metadataParts.isEmpty {
                    Text("•")
                        .kicker(color: metadataColor)
                        .accessibilityHidden(true)
                }

                Image(systemName: "bubble.left")
                    .font(.appSymbol(size: 11, weight: .medium))
                    .foregroundStyle(metadataColor)
                    .accessibilityHidden(true)

                Text(comments)
                    .monospacedDigit()
                    .kicker(color: metadataColor)
            }
        }
        .lineLimit(1)
    }
}

struct DayDelimiter: View, Equatable {
    let item: ContentSummary
    let isFirst: Bool

    private static let monthDayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d"
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    private var dayLabel: String {
        guard let date = item.itemDate else { return "" }
        let calendar = Calendar.current

        if calendar.isDateInToday(date) {
            return "TODAY"
        } else if calendar.isDateInYesterday(date) {
            return "YESTERDAY"
        } else {
            return Self.monthDayFormatter.string(from: date).uppercased()
        }
    }

    var body: some View {
        HStack(spacing: 10) {
            Text(dayLabel)
                .kicker(color: .sectionDelimiter)

            Rectangle()
                .fill(Color.outlineVariant)
                .frame(height: 1)
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, isFirst ? 12 : 20)
        .padding(.bottom, 7)
        .background(Color.surfacePrimary)
    }
}
