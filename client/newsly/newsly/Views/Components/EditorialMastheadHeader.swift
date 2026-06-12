//
//  EditorialMastheadHeader.swift
//  newsly
//
//  Tab-level masthead: small uppercase date subhead above the page title.
//

import SwiftUI

struct EditorialMastheadHeader: View {
    let title: String
    var subtitle: String? = nil
    var date: Date = AppClock.now
    var trailingAccessory: AnyView? = nil

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
        VStack(alignment: .leading, spacing: 6) {
            Text(dateLabel)
                .kicker()

            HStack(alignment: .firstTextBaseline) {
                Text(title)
                    .font(.terracottaDisplayLarge)
                    .foregroundStyle(Color.onSurface)
                    .frame(maxWidth: .infinity, alignment: .leading)

                if let trailingAccessory {
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
