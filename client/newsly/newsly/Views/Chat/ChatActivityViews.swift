//
//  ChatActivityViews.swift
//  newsly
//

import SwiftUI

struct ThinkingBubbleView: View {
    let startDate: Date?
    let statusText: String?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isAnimating = false

    private func elapsedSeconds(at date: Date) -> Int {
        guard let startDate else { return 0 }
        return max(Int(date.timeIntervalSince(startDate)), 0)
    }

    private func formattedDuration(elapsedSeconds: Int) -> String {
        String(format: "%02d:%02d", elapsedSeconds / 60, elapsedSeconds % 60)
    }

    var body: some View {
        TimelineView(.periodic(from: startDate ?? Date(), by: 1.0)) { timeline in
            let elapsedSeconds = elapsedSeconds(at: timeline.date)
            content(elapsedSeconds: elapsedSeconds)
        }
    }

    private func content(elapsedSeconds: Int) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                HStack(spacing: 6) {
                    ForEach(0..<3) { index in
                        Circle()
                            .fill(Color.chatAccent.opacity(0.5))
                            .frame(width: 6, height: 6)
                            .offset(y: reduceMotion ? 0 : (isAnimating ? -2 : 2))
                            .animation(
                                reduceMotion ? nil : AppMotion.typingDotPulse
                                    .delay(Double(index) * 0.1),
                                value: isAnimating
                            )
                    }
                }

                Text(formattedDuration(elapsedSeconds: elapsedSeconds))
                    .font(.appCaption2)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .monospacedDigit()
            }

            if let statusText, !statusText.isEmpty {
                Text(statusText)
                    .font(.appCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(Color.surfaceContainer)
        .clipShape(UnevenRoundedRectangle(topLeadingRadius: 4, bottomLeadingRadius: 16, bottomTrailingRadius: 16, topTrailingRadius: 16))
        .frame(maxWidth: .infinity, alignment: .leading)
        .onAppear {
            guard !reduceMotion else { return }
            isAnimating = true
        }
        .onChange(of: reduceMotion) { _, reduceMotion in
            isAnimating = !reduceMotion
        }
    }
}

struct InitialSuggestionsLoadingView: View {
    var body: some View {
        VStack(spacing: 18) {
            BuddyLoadingIndicator(size: 72)

            VStack(spacing: 6) {
                Text("Preparing suggestions")
                    .font(.appHeadline)
                    .foregroundStyle(Color.onSurface)

                Text("Analyzing the article for you")
                    .font(.appSubheadline)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
    }
}

#Preview("Loading State") {
    InitialSuggestionsLoadingView()
}
