//
//  EditorialMastheadHeader.swift
//  newsly
//
//  Tab-level masthead: small uppercase date subhead above the page title.
//

import SwiftUI

struct EditorialMastheadHeader: View {
    let title: String
    var titleAccessibilityIdentifier: String? = nil
    var subtitle: String? = nil
    var date: Date = AppClock.now
    var showsDate = true
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

    private var titleText: some View {
        Text(title)
            .accessibilityIdentifier(ifPresent: titleAccessibilityIdentifier)
            .font(.terracottaDisplayLarge)
            .foregroundStyle(Color.onSurface)
            .lineLimit(1)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if showsDate {
                Text(dateLabel)
                    .kicker()
            }

            // Title and accessory share a row when they fit. In the open
            // display's 300 pt sidebar, or at large text sizes, the accessory
            // moves below the title so the title never breaks mid-word.
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .center, spacing: 12) {
                    titleText.fixedSize()
                    Spacer(minLength: 0)
                    trailingAccessory
                }
                VStack(alignment: .leading, spacing: 10) {
                    titleText
                        .minimumScaleFactor(0.6)
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
