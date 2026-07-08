//
//  EditorialMastheadHeader.swift
//  newsly
//
//  Tab-level masthead: small uppercase date subhead above the page title.
//

import SwiftUI

enum MastheadAccessoryAlignment {
    /// Pinned to the top-right corner, level with the date kicker.
    case top
    /// Centered on the title line.
    case title
}

struct EditorialMastheadHeader: View {
    let title: String
    var subtitle: String? = nil
    var date: Date = AppClock.now
    var trailingAccessory: AnyView? = nil
    var accessoryAlignment: MastheadAccessoryAlignment = .top

    private static let mastheadFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "EEEE, MMM d"
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    private var dateLabel: String {
        Self.mastheadFormatter.string(from: date).uppercased()
    }

    var body: some View {
        ZStack(alignment: .topTrailing) {
            VStack(alignment: .leading, spacing: 6) {
                Text(dateLabel)
                    .kicker()

                HStack(alignment: .center, spacing: 12) {
                    Text(title)
                        .font(.terracottaDisplayLarge)
                        .foregroundStyle(Color.onSurface)
                        .frame(maxWidth: .infinity, alignment: .leading)

                    if accessoryAlignment == .title, let trailingAccessory {
                        trailingAccessory
                    }
                }

                if let subtitle {
                    Text(subtitle)
                        .font(.terracottaHeadlineSmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            // Pin the accessory to the top-right corner so it reads as an
            // upper-right affordance rather than sitting on the title baseline.
            if accessoryAlignment == .top, let trailingAccessory {
                trailingAccessory
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 16)
        .padding(.bottom, 24)
    }
}

#Preview {
    VStack(alignment: .leading, spacing: 0) {
        EditorialMastheadHeader(title: "Long Read")
        EditorialMastheadHeader(title: "Fast Read")
        EditorialMastheadHeader(title: "Knowledge")
        Spacer()
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    .background(Color.surfacePrimary)
}
