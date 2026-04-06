//
//  NewsGroupCard.swift
//  newsly
//
//  Created by Assistant on 10/12/25.
//

import SwiftUI

struct NewsGroupCard: View {
    let group: NewsGroup
    var isCurrent: Bool = false

    var body: some View {
        let content = VStack(alignment: .leading, spacing: 0) {
            // News items
            ForEach(group.items) { item in
                NavigationLink(destination: ContentDetailView(contentId: item.id, allContentIds: group.items.map { $0.id })) {
                    VStack(alignment: .leading, spacing: 4) {
                        // Title - full display
                        Text(item.displayTitle)
                            .font(.body)
                            .fontWeight(.medium)
                            .foregroundColor(item.isRead ? .secondary : .primary)
                            .lineLimit(nil)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .multilineTextAlignment(.leading)

                        // Short summary if available
                        if let summary = item.shortSummary, !summary.isEmpty {
                            Text(summary)
                                .font(.footnote)
                                .foregroundColor(.secondary)
                                .lineLimit(2)
                                .fixedSize(horizontal: false, vertical: true)
                        }

                        // Metadata row
                        HStack(spacing: 6) {
                            // Platform icon and source
                            HStack(spacing: 3) {
                                PlatformIcon(platform: item.platform)
                                    .opacity(item.platform == nil ? 0 : 1)
                                if let source = item.source {
                                    Text(source)
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                        .lineLimit(1)
                                }
                            }

                            Spacer()

                            // Date
                            ContentTimestampText(rawValue: item.primaryTimestamp, style: .compactRelative)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    .padding(.vertical, 8)
                }
                .buttonStyle(.plain)

                if item.id != group.items.last?.id {
                    Divider()
                }
            }
        }
        // Measure intrinsic content height BEFORE parent applies frame constraints
        .background(
            Group {
                if isCurrent {
                    GeometryReader { proxy in
                        Color.clear
                            .preference(key: GroupHeightPreferenceKey.self,
                                        value: proxy.size.height)
                    }
                }
            }
        )

        content
            .opacity(group.isRead ? 0.7 : 1.0)
    }
}
