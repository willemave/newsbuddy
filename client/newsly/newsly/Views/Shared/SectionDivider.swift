//
//  SectionDivider.swift
//  newsly
//
//  Subtle divider between rows inside a settings card.
//

import SwiftUI

struct RowDivider: View {
    var leadingInset: CGFloat = Spacing.rowDividerInset

    var body: some View {
        Divider()
            .padding(.leading, leadingInset)
    }
}

#Preview {
    VStack(spacing: 0) {
        Text("Row 1")
            .padding()
        RowDivider()
        Text("Row 2")
            .padding()
    }
}
