//
//  KnowledgeSaveIcon.swift
//  newsly
//

import SwiftUI

struct KnowledgeSaveIcon: View {
    let isSaved: Bool
    var size: CGFloat = 20
    var savedColor: Color = .brandTertiary
    var unsavedColor: Color = .secondary
    var badgeColor: Color = .brandTertiary

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Image(systemName: isSaved ? "books.vertical.fill" : "books.vertical")
                .font(.system(size: size, weight: .regular))
                .foregroundStyle(isSaved ? savedColor : unsavedColor)

            if !isSaved {
                ZStack {
                    Circle()
                        .fill(badgeColor)
                    Image(systemName: "plus")
                        .font(.system(size: max(size * 0.34, 7), weight: .bold))
                        .foregroundStyle(.white)
                }
                .frame(width: max(size * 0.52, 10), height: max(size * 0.52, 10))
                .offset(x: size * 0.22, y: -size * 0.18)
            }
        }
        .frame(width: size + 8, height: size + 8)
    }
}
