//
//  KnowledgeSaveIcon.swift
//  newsly
//

import SwiftUI

struct KnowledgeSaveIcon: View {
    let isSaved: Bool
    var size: CGFloat = 20
    var savedColor: Color = .brandPrimary
    var unsavedColor: Color = .onSurfaceSecondary
    var badgeColor: Color = .brandPrimary
    var badgeForegroundColor: Color = .white

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Image(systemName: isSaved ? "books.vertical.fill" : "books.vertical")
                .font(.appSymbol(size: size, weight: .regular))
                .foregroundStyle(isSaved ? savedColor : unsavedColor)

            if !isSaved {
                ZStack {
                    Circle()
                        .fill(badgeColor)
                    Image(systemName: "plus")
                        .font(.appSymbol(size: max(size * 0.34, 7), weight: .bold))
                        .foregroundStyle(badgeForegroundColor)
                }
                .frame(width: max(size * 0.52, 10), height: max(size * 0.52, 10))
                .offset(x: size * 0.22, y: -size * 0.18)
            }
        }
        .frame(width: size + 8, height: size + 8)
    }
}
