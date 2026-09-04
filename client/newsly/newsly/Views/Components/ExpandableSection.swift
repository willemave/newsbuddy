//
//  ExpandableSection.swift
//  newsly
//

import SwiftUI

struct ExpandableSection<Content: View>: View {
    private let title: String
    private let icon: String
    @Binding private var isExpanded: Bool
    private let content: Content

    init(
        title: String,
        icon: String,
        isExpanded: Binding<Bool>,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.icon = icon
        _isExpanded = isExpanded
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: toggleExpanded) {
                HStack {
                    HStack(spacing: 8) {
                        Image(systemName: icon)
                            .font(.readerBody.weight(.bold))
                            .foregroundColor(Color.onSurface)

                        Text(title.uppercased())
                            .font(.readerBody.weight(.bold))
                            .foregroundColor(Color.onSurface)
                            .tracking(0.4)
                    }

                    Spacer()

                    Image(systemName: "chevron.right")
                        .font(.appCaption2)
                        .fontWeight(.bold)
                        .foregroundColor(Color.onSurfaceTertiary)
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                }
                .padding(ExpandableSectionDesign.cardPadding)
            }
            .buttonStyle(.plain)

            if isExpanded {
                content
                    .padding(.horizontal, ExpandableSectionDesign.cardPadding)
                    .padding(.bottom, ExpandableSectionDesign.cardPadding)
            }
        }
        .background(Color.surfaceSecondary)
        .clipShape(RoundedRectangle(cornerRadius: ExpandableSectionDesign.cardRadius))
    }

    private func toggleExpanded() {
        withAnimation(AppMotion.subtle) {
            isExpanded.toggle()
        }
    }
}

private enum ExpandableSectionDesign {
    static let cardPadding: CGFloat = 16
    static let cardRadius: CGFloat = 14
}
