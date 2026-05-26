//
//  DownloadMoreMenu.swift
//  newsly
//

import SwiftUI

struct DownloadMoreMenu: View {
    let title: String
    let counts: [Int]
    let onSelect: (Int) -> Void

    init(
        title: String = "Load more",
        counts: [Int] = [3, 5, 10, 20],
        onSelect: @escaping (Int) -> Void
    ) {
        self.title = title
        self.counts = counts
        self.onSelect = onSelect
    }

    var body: some View {
        Menu {
            ForEach(counts, id: \.self) { count in
                Button("\(count) items") {
                    onSelect(count)
                }
            }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "arrow.down")
                    .font(.system(size: 14, weight: .regular))
                Text(title)
                    .font(.subheadline)
            }
            .foregroundColor(Color.onSurfaceSecondary)
        }
        .buttonStyle(.plain)
    }
}
