//
//  ShortFormSetupEmptyState.swift
//  newsly
//

import SwiftUI

struct ShortFormSetupEmptyState: View {
    let processingCount: Int
    let crawlingSourceCount: Int

    private var title: String {
        if processingCount > 0 {
            return "Preparing \(processingCount) Fast \(processingCount == 1 ? "Read" : "Reads")"
        }
        return "Crawling \(crawlingSourceCount) \(crawlingSourceCount == 1 ? "Source" : "Sources")"
    }

    private var subtitle: String {
        if processingCount > 0 && crawlingSourceCount > 0 {
            return "We're checking your sources and summarizing new items as they arrive."
        }
        if processingCount > 0 {
            return "Summaries will appear here as soon as processing finishes."
        }
        return "We're checking your selected sources now. Fast Reads will appear as soon as the first item is ready."
    }

    var body: some View {
        VStack(spacing: 16) {
            ProgressView()
                .controlSize(.regular)

            VStack(spacing: 4) {
                Text(title)
                    .font(.listTitle.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                    .multilineTextAlignment(.center)

                Text(subtitle)
                    .font(.listSubtitle)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 280)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
    }
}
