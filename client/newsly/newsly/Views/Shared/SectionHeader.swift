//
//  SectionHeader.swift
//  newsly
//
//  Linear-style section header with uppercase label and optional trailing action.
//

import SwiftUI

struct SectionHeader: View {
    let title: String
    var action: (() -> Void)? = nil
    var actionLabel: String? = nil

    var body: some View {
        HStack {
            Text(title.uppercased())
                .font(.sectionHeader)
                .foregroundStyle(Color.onSurfaceSecondary)
                .tracking(0.5)

            Spacer()

            if let action, let actionLabel {
                Button(action: action) {
                    Text(actionLabel)
                        .font(.appCaption)
                        .foregroundStyle(.tint)
                }
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, Spacing.sectionTop)
        .padding(.bottom, Spacing.sectionBottom)
    }
}

#Preview {
    VStack(spacing: 0) {
        SectionHeader(title: "Account")
        SectionHeader(title: "Sources", action: {}, actionLabel: "Add")
    }
    .background(Color.surfacePrimary)
}
