//
//  ReaderSectionHeader.swift
//  newsly
//
//  Option A editorial section header used across the reader summary surfaces
//  (Fast Read / Long Read): a short accent overline rule above a serif
//  title, with no leading icon. The accent lives only in the rule; the title
//  text stays monochrome per the single-accent doctrine.
//
//  An optional trailing accessory hosts inline controls (a disclosure chevron,
//  the comments action buttons). Provide your own Spacer inside the accessory
//  closure to push it to the trailing edge.
//

import SwiftUI

struct ReaderSectionHeader<Accessory: View>: View {
    private let title: String
    @ViewBuilder private let accessory: () -> Accessory

    init(_ title: String, @ViewBuilder accessory: @escaping () -> Accessory) {
        self.title = title
        self.accessory = accessory
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Capsule(style: .continuous)
                .fill(Color.brandPrimary)
                .frame(width: 26, height: 2.5)
                .accessibilityHidden(true)

            HStack(alignment: .center, spacing: 12) {
                Text(title)
                    .font(.appTitle3)
                    .foregroundColor(Color.onSurface)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)

                accessory()
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension ReaderSectionHeader where Accessory == EmptyView {
    init(_ title: String) {
        self.init(title) { EmptyView() }
    }
}

#Preview {
    VStack(alignment: .leading, spacing: 24) {
        ReaderSectionHeader("Key Points")

        ReaderSectionHeader("Comments") {
            Spacer(minLength: 10)
            Image(systemName: "bubble.left.and.bubble.right")
                .foregroundColor(Color.onSurfaceSecondary)
            Image(systemName: "arrow.up.right.square")
                .foregroundColor(Color.onSurfaceSecondary)
        }

        ReaderSectionHeader("Takeaway")
    }
    .padding()
    .background(Color.surfacePrimary)
}
